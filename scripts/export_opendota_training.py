#!/usr/bin/env python3
"""Export official-role training rows from a stopped, provenance-checked cohort."""
from __future__ import annotations

import argparse
import collections
import fcntl
import gzip
import hashlib
import json
import pathlib
import sqlite3
import sys

from collect_opendota import (API, POSITION_SOURCE_OFFICIAL, atomic_bytes, integer,
                             normalize_match, utc_now, write_json)


def export_training(source, output, patch, min_duration=720):
    source, output = pathlib.Path(source).resolve(), pathlib.Path(output).resolve()
    if source == output or source in output.parents:
        raise ValueError('export output must be outside the collection directory')
    if not integer(patch) or patch < 0 or not integer(min_duration) or min_duration < 0:
        raise ValueError('invalid patch or minimum duration')
    config = json.loads((source / 'config.json').read_text())
    if config.get('source') != 'ranked' or config.get('role_source') != 'official':
        raise ValueError('export requires a separate official ranked cohort')
    rows, accepted, rejections, integrity_errors = [], [], collections.Counter(), []
    with (source / '.collector.lock').open('r') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('stop the collector before exporting') from exc
        db = sqlite3.connect(f'{(source / "state.sqlite3").as_uri()}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            candidates = db.execute('SELECT * FROM candidates WHERE status="ok" ORDER BY match_id DESC').fetchall()
            failed = [dict(r) for r in db.execute('SELECT match_id,error FROM candidates WHERE status="failed" ORDER BY match_id DESC')]
            for candidate in candidates:
                mid = candidate['match_id']
                try:
                    cached = source / f'raw/matches/{mid}.json.gz'
                    provenance = json.loads((source / f'raw/matches/{mid}.source.json').read_text())
                    payload = gzip.decompress(cached.read_bytes())
                    digest = hashlib.sha256(payload).hexdigest()
                    request = db.execute('SELECT * FROM requests WHERE id=?', (provenance['request_id'],)).fetchone()
                    expected_url = f'{API}/matches/{mid}?meta=1'
                    if (provenance['url'] != expected_url or provenance['sha256'] != digest or request is None
                            or request['status'] != 200 or request['match_id'] != mid or request['kind'] != 'detail'
                            or request['url'] != expected_url or request['sha256'] != digest
                            or request['raw_path'] != provenance['raw_path']):
                        raise ValueError('response provenance mismatch')
                    raw_http = (source / provenance['raw_path']).resolve()
                    if source not in raw_http.parents or raw_http.read_bytes() != cached.read_bytes():
                        raise ValueError('cached response differs from HTTP archive')
                    raw = json.loads(payload)
                    normalized = normalize_match(raw, json.loads(candidate['summary']), 'official')
                    if normalized != json.loads(candidate['normalized']):
                        raise ValueError('stored normalization differs from raw response')
                except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
                    integrity_errors.append({'match_id': mid, 'error': str(exc)})
                    continue
                match = normalized['match']
                reasons = []
                if not match['basic_ok']:
                    reasons.append('invalid_match_context_slots_or_heroes')
                if not match['official_roles_complete']:
                    reason = match['official_role_error'] or 'official_roles_incomplete'
                    reasons.append(reason)
                if match['patch'] != patch:
                    reasons.append('other_patch')
                if not integer(match['duration']) or match['duration'] < min_duration:
                    reasons.append('short_or_unknown_duration')
                players = raw.get('players')
                if (not isinstance(players, list) or len(players) != 10
                        or any(not isinstance(p, dict) or not integer(p.get('leaver_status'))
                               or p['leaver_status'] not in (0, 1) for p in players)):
                    reasons.append('leaver_status_not_0_or_1')
                if reasons:
                    rejections.update(reasons)
                    continue
                ordered = {side: [p['hero_id'] for p in sorted(normalized['players'], key=lambda p: p['position'])
                                  if p['side'] == side] for side in ('radiant', 'dire')}
                rows.append({key: match[key] for key in ('match_id', 'start_time', 'game_mode', 'lobby_type',
                                                        'patch', 'rank_band_est', 'rank_known_players')}
                            | ordered | {'radiant_win': match['radiant_win'], 'position_source': POSITION_SOURCE_OFFICIAL})
                accepted.append({'match_id': mid, 'sha256': digest, 'source_url': expected_url,
                                 'raw_path': f'raw/matches/{mid}.json.gz'})
        finally:
            db.close()
    payload = ''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n' for row in rows).encode()
    report = {
        'created_at': utc_now(), 'source_directory': source.name,
        'position_source': POSITION_SOURCE_OFFICIAL, 'patch': patch, 'min_duration_seconds': min_duration,
        'successful_details': len(candidates), 'accepted_matches': len(rows),
        'rejected_matches': len(candidates) - len(rows) - len(integrity_errors),
        'rejection_reason_counts_overlap': dict(rejections), 'integrity_errors': integrity_errors,
        'failed_detail_ids': failed, 'dataset_sha256': hashlib.sha256(payload).hexdigest(),
        'start_time_range': [min(r['start_time'] for r in rows), max(r['start_time'] for r in rows)] if rows else None,
        'rank_band_counts': dict(collections.Counter(r['rank_band_est'] or 'unknown' for r in rows)),
        'rank_known_players_counts': dict(collections.Counter(str(r['rank_known_players']) for r in rows)),
    }
    atomic_bytes(output / 'matches.jsonl', payload)  # Also replace stale files on zero eligible rows.
    write_json(output / 'provenance.json', {'source_config': config, 'accepted': accepted})
    write_json(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--patch', required=True, type=int)
    parser.add_argument('--min-duration', type=int, default=720)
    args = parser.parse_args(argv)
    report = export_training(args.source, args.output, args.patch, args.min_duration)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['integrity_errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
