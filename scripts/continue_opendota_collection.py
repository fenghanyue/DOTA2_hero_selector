#!/usr/bin/env python3
"""Collect one quota-bounded ranked batch and count verified OneDrive datasets."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import importlib.util
import io
import json
import pathlib
import re
import shutil
import sqlite3
import subprocess
import uuid

from collect_opendota import Collector, write_json
from export_opendota_training import export_training
from audit_opendota_official import audit

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = pathlib.Path('/workspace/.cloud-environment/onedrive-tools/onedrive_backup.py')
HELPER_SHA = '3bbb9dc61f8e1e25cc2432514be128265ebc833940760a3bde09e130c0367408'
REMOTE_ROOT = 'dota_onedrive:DOTA2_hero_selector/opendota'
ORIGINAL = 'ranked-official-20261112-1000'
ORIGINAL_MANIFEST_SHA = '317b8db8f3b95fe5973a325376a38d018ddb4103c22b75c3e8eab2fa92156d73'
BATCH_PATTERN = re.compile(r'ranked-official-\d{8}T(?:00|06|12|18)$')
TARGET = 50000
PATCH = 60
DETAILS_PER_SLOT = 600
REQUESTS_PER_SLOT = 675  # Four UTC slots <= 2700; leave 300 of the free 3000.
READ_METHOD = 'graph-contentstream'


class ContinuationError(Exception):
    pass


def load_helper():
    if not HELPER.is_file() or hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ContinuationError('Published OneDrive helper is missing or has a different SHA-256.')
    spec = importlib.util.spec_from_file_location('published_onedrive', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_slot(now):
    now = now.astimezone(dt.timezone.utc)
    return now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)


def slot_batch(now):
    return 'ranked-official-' + utc_slot(now).strftime('%Y%m%dT%H')


def merge_rows(datasets):
    """Identical copies count once; conflicting records must be investigated."""
    unique = {}
    duplicates = 0
    for rows in datasets:
        for row in rows:
            mid = row.get('match_id')
            if (type(mid) is not int or mid <= 0 or row.get('patch') != PATCH
                    or row.get('game_mode') != 22 or row.get('lobby_type') != 7
                    or row.get('position_source') != 'valve_metadata_lane_selection_flags'
                    or type(row.get('radiant_win')) is not bool
                    or type(row.get('start_time')) is not int
                    or any(not isinstance(row.get(side), list) or len(row[side]) != 5
                           for side in ('radiant', 'dire'))):
                raise ContinuationError('Invalid training row in a completed batch.')
            heroes = row['radiant'] + row['dire']
            if any(type(h) is not int or h <= 0 for h in heroes) or len(set(heroes)) != 10:
                raise ContinuationError('Invalid heroes in a completed batch.')
            if mid in unique:
                if unique[mid] != row:
                    raise ContinuationError(f'Conflicting copies of match {mid}.')
                duplicates += 1
            unique[mid] = row
    return unique, duplicates


def checked_artifact(backup, config, batch, manifest, name, cache):
    entry = next((e for e in manifest['files'] if e['path'] == 'training/' + name), None)
    if entry is None:
        raise ContinuationError(f'Completed batch lacks {name}: {batch}')
    path = cache / batch / name
    if path.is_file() and path.stat().st_size == entry['bytes'] and backup.sha256_file(path) == entry['sha256']:
        return path.read_bytes()
    buffer = io.BytesIO()
    backup.check_file(config, backup.batch_prefix(batch), entry, READ_METHOD, buffer)
    payload = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def inventory(backup, config, cache):
    directories = json.loads(backup.run_rclone(config, 'lsjson', REMOTE_ROOT, '--dirs-only'))
    names = sorted(item['Name'] for item in directories
                   if item.get('IsDir') and (item['Name'] == ORIGINAL or BATCH_PATTERN.fullmatch(item['Name'])))
    if ORIGINAL not in names:
        raise ContinuationError('Original 830-match batch is missing; refusing to reset the count.')
    datasets, completed, unfinished = [], [], []
    patch_changed = False
    for batch in names:
        loaded = backup.load_manifest(config, batch, READ_METHOD, missing_ok=True)
        if loaded is None:
            if batch == ORIGINAL:
                raise ContinuationError('Original completion manifest is missing.')
            unfinished.append(batch)
            continue
        manifest, payload, manifest_sha = loaded
        if batch == ORIGINAL and manifest_sha != ORIGINAL_MANIFEST_SHA:
            raise ContinuationError('Original completion manifest SHA-256 differs.')
        dataset = checked_artifact(backup, config, batch, manifest, 'matches.jsonl', cache)
        report = json.loads(checked_artifact(backup, config, batch, manifest, 'report.json', cache))
        audited = json.loads(checked_artifact(backup, config, batch, manifest, 'audit.json', cache))
        rows = [json.loads(line) for line in dataset.splitlines()]
        merge_rows([rows])
        if (report.get('dataset_sha256') != hashlib.sha256(dataset).hexdigest()
                or report.get('accepted_matches') != len(rows) or report.get('integrity_errors')
                or audited.get('passed') is not True or audited.get('training_matches_checked') != len(rows)
                or len({r['match_id'] for r in rows}) != len(rows)):
            raise ContinuationError(f'Count, export SHA-256, or audit mismatch: {batch}')
        datasets.append(rows)
        patch_changed |= bool(report.get('rejection_reason_counts_overlap', {}).get('other_patch'))
        completed.append({'batch': batch, 'accepted_matches': len(rows), 'manifest_sha256': manifest_sha})
    unique, duplicates = merge_rows(datasets)
    return unique, {'completed_batches': completed, 'unfinished_batches': unfinished,
                    'duplicate_rows_not_counted': duplicates, 'patch_changed': patch_changed}


def claim_slot(backup, config, batch, source):
    """A lost worker consumes its slot; a fresh worker must not spend it twice."""
    local = source / 'run-claim.json'
    remote = backup.batch_prefix(batch) + '/run-claim.json'
    buffer = io.BytesIO()
    try:
        backup.read_remote(config, remote, buffer, READ_METHOD, 8192)
    except backup.RemoteMissing:
        pass
    else:
        if not local.is_file() or local.read_bytes() != buffer.getvalue():
            raise ContinuationError('slot_already_claimed_without_local_state')
        return
    if not local.exists():
        write_json(local, {'batch': batch, 'claim_id': uuid.uuid4().hex,
                           'request_limit': REQUESTS_PER_SLOT, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    backup.run_rclone(config, 'copyto', str(local), remote, *backup.UPLOAD_FLAGS)
    if backup.remote_sha256(config, remote, READ_METHOD) != backup.sha256_file(local):
        raise ContinuationError('Slot reservation readback differs.')


class DeduplicatingCollector(Collector):
    def __init__(self, args, known_ids):
        super().__init__(args)
        self.known_ids = known_ids

    def discover(self):
        super().discover()
        repeated = [r['match_id'] for r in self.db.execute(
            'SELECT match_id FROM candidates WHERE status="pending" AND attempted=0')
            if r['match_id'] in self.known_ids]
        self.db.executemany('DELETE FROM candidates WHERE match_id=? AND attempted=0', [(mid,) for mid in repeated])
        self.db.commit()
        filters = self.get_meta('summary_filters', {})
        filters['already_in_verified_training'] = filters.get('already_in_verified_training', 0) + len(repeated)
        self.set_meta('summary_filters', filters)


def collect(source, known_ids):
    remaining = TARGET - len(known_ids)
    if remaining <= 0:
        return
    report_path = source / 'report.json'
    if report_path.exists():
        previous = json.loads(report_path.read_text())
        if previous['status'] != 'running':
            return  # Seal this slot, including any quota/rate-limited partial batch.
    args = argparse.Namespace(output=str(source), source='ranked', detail_limit=min(DETAILS_PER_SLOT, remaining),
                              rpm=40, daily_budget=REQUESTS_PER_SLOT, daily_reserve=300,
                              request_limit=REQUESTS_PER_SLOT, min_duration=720,
                              min_age_hours=2, max_age_days=7, max_list_pages=6)
    collector = DeduplicatingCollector(args, known_ids)
    try:
        # A killed worker may have logged a request before its response arrived.
        # Keep that request charged; do not invent a response or a successful row.
        collector.db.execute('UPDATE requests SET error=? WHERE status IS NULL AND error IS NULL',
                             ('interrupted_before_response_recorded',))
        collector.db.commit()
        collector.run()
    finally:
        collector.db.close()
        collector.lock.close()


def prepare(backup, batch, source, work, known_ids):
    training, archive = work / 'training' / batch, work / 'archives' / (batch + '.tar.zst')
    prepared = work / 'prepared' / (batch + '.json')
    if prepared.exists():
        plan = json.loads(prepared.read_text())
        for entry in plan['files']:
            path = pathlib.Path(entry['path'])
            if not path.is_file() or backup.sha256_file(path) != entry['sha256']:
                raise ContinuationError('Prepared batch changed after it was sealed.')
        return training, archive
    stats = json.loads((source / 'report.json').read_text())
    if stats['status'] in ('running', 'failed') or not stats['successful_detail_responses']:
        raise ContinuationError('No stopped, usable collection to back up.')
    exported = export_training(source, training, PATCH)
    audited = audit(source, training)
    write_json(training / 'audit.json', audited)
    if exported['integrity_errors'] or not audited['passed']:
        raise ContinuationError('Independent audit failed; batch is not counted or uploaded.')
    shutil.copyfile(source / 'report.json', training / 'collection-report.json')
    rows = [json.loads(line) for line in (training / 'matches.jsonl').read_bytes().splitlines()]
    unique, duplicates = merge_rows([list(known_ids.values()), rows])
    write_json(training / 'collection-summary.json', {
        'target_qualified_unique_matches': TARGET, 'baseline_checkpoint': 5000,
        'previous_verified_unique_matches': len(known_ids), 'accepted_in_batch': len(rows),
        'new_unique_matches': len(unique) - len(known_ids), 'duplicates_not_counted': duplicates,
        'projected_verified_unique_after_upload': len(unique), 'patch': PATCH,
        'patch_changed': any(int(p) != PATCH for p in stats['patch_counts']),
        'start_time_range': exported['start_time_range'], 'http_requests': stats['http_requests'],
        'request_budget_per_utc_slot': REQUESTS_PER_SLOT, 'slots_per_utc_day': 4,
        'sampling': 'Latest public ranked all-pick IDs in four separated UTC slots per day; not a random sample.',
        'qualified_count_committed_only_after_upload_and_independent_verify': True})
    archive.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['tar', '--zstd', '-cf', str(archive), '-C', str(source.parent), source.name],
                   check=True, env=backup.child_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if archive.stat().st_size > backup.SINGLE_UPLOAD_LIMIT:
        raise ContinuationError('Archive exceeds the accepted 200 MiB limit.')
    files = [archive] + sorted(p for p in training.iterdir() if p.is_file())
    write_json(prepared, {'files': [{'path': str(p), 'sha256': backup.sha256_file(p)} for p in files]})
    return training, archive


def run(work, batch=None):
    backup = load_helper()
    config = backup.restore_config()
    known, before = inventory(backup, config, work / 'cache')
    result = {'target': TARGET, 'verified_unique_before': len(known), **before}
    if len(known) >= TARGET:
        return result | {'status': 'target_reached', 'verified_unique_matches': len(known)}
    if before.get('patch_changed'):
        return result | {'status': 'patch_changed', 'verified_unique_matches': len(known)}
    batch = batch or slot_batch(dt.datetime.now(dt.timezone.utc))
    if not BATCH_PATTERN.fullmatch(batch):
        raise ContinuationError('Invalid six-hour UTC slot batch.')
    try:
        dt.datetime.strptime(batch, 'ranked-official-%Y%m%dT%H')
    except ValueError:
        raise ContinuationError('Invalid slot date.') from None
    if batch in {b['batch'] for b in before['completed_batches']}:
        return result | {'status': 'slot_complete', 'verified_unique_matches': len(known), 'batch': batch}
    source = ROOT / 'data/opendota' / batch
    if batch != slot_batch(dt.datetime.now(dt.timezone.utc)):
        prior_report = source / 'report.json'
        if not prior_report.exists() or json.loads(prior_report.read_text())['status'] == 'running':
            raise ContinuationError('Only the current UTC slot may issue new OpenDota requests.')
    claim_slot(backup, config, batch, source)
    collect(source, known)
    training, archive = prepare(backup, batch, source, work, known)
    backup.upload_batch(config, archive, training, batch, work / 'reports' / (batch + '-upload.json'), READ_METHOD)
    backup.verify_batch(config, batch, work / 'reports' / (batch + '-verify.json'), READ_METHOD)
    after, checked = inventory(backup, config, work / 'cache')
    summary = json.loads((training / 'collection-summary.json').read_text())
    times = [r['start_time'] for r in after.values()]
    return result | {'status': 'target_reached' if len(after) >= TARGET else 'batch_verified',
                     'batch': batch, 'verified_unique_matches': len(after),
                     'new_verified_unique_matches': len(after) - len(known),
                     'baseline_checkpoint_reached': len(after) >= 5000,
                     'patch_changed': summary['patch_changed'],
                     'start_time_range_utc': [dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
                                              for t in (min(times), max(times))] if times else None,
                     'rank_band_counts': dict(collections.Counter(r['rank_band_est'] or 'unknown' for r in after.values())),
                     'verified_inventory': checked, 'vault_update_required': backup.vault_update_required(config)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=pathlib.Path, default=ROOT / 'data/opendota-continuation')
    parser.add_argument('--batch', help='Finish or resume an existing local slot instead of the current slot.')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    with (args.work / '.continuation.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run(args.work, args.batch)
        except BlockingIOError:
            print(json.dumps({'status': 'paused', 'reason': 'another_local_continuation_is_running'}))
            return 75
        except Exception as exc:
            # Never print subprocess stderr, credential configuration, or signed URLs.
            reason = str(exc) if isinstance(exc, ContinuationError) else type(exc).__name__
            print(json.dumps({'status': 'blocked', 'reason': reason}))
            return 1
        write_json(args.work / 'latest-result.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
