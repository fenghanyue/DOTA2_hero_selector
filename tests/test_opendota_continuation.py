import argparse
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import continue_opendota_collection as continuation
import onedrive_backup as backup
from test_onedrive_backup import local_batch, memory_remote


def row(mid=1):
    return {'match_id': mid, 'start_time': 1794559173, 'patch': 60, 'game_mode': 22,
            'lobby_type': 7, 'position_source': 'valve_metadata_lane_selection_flags',
            'radiant': [1, 2, 3, 4, 5], 'dire': [6, 7, 8, 9, 10], 'radiant_win': False,
            'rank_band_est': '传奇', 'rank_known_players': 10}


class ContinuationTests(unittest.TestCase):
    def test_four_slots_bound_daily_requests_and_use_utc(self):
        slots = {continuation.slot_batch(dt.datetime(2026, 11, 13, h, tzinfo=dt.timezone.utc))
                 for h in range(24)}
        self.assertEqual(len(slots), 4)
        self.assertEqual(len(slots) * continuation.REQUESTS_PER_SLOT, 2700)
        local = dt.datetime(2026, 11, 13, 8, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.assertEqual(continuation.slot_batch(local), 'ranked-official-20261113T00')

    def test_copies_count_once_and_false_winner_survives(self):
        unique, duplicates = continuation.merge_rows([[row(1), row(2)], [row(1)]])
        self.assertEqual(set(unique), {1, 2})
        self.assertEqual(duplicates, 1)
        self.assertIs(unique[1]['radiant_win'], False)

    def test_conflicting_copies_do_not_increase_the_count(self):
        changed = row()
        changed['radiant_win'] = True
        with self.assertRaises(continuation.ContinuationError):
            continuation.merge_rows([[row()], [changed]])

    def test_wrong_patch_inferred_roles_and_duplicate_heroes_are_rejected(self):
        for field, value in [('patch', 61), ('position_source', 'opendota_position_est'),
                             ('match_id', True), ('radiant_win', 0), ('dire', [1, 7, 8, 9, 10])]:
            changed = row()
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(continuation.ContinuationError):
                continuation.merge_rows([[changed]])

    def test_target_stops_before_claiming_or_collecting(self):
        backup = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'TARGET', 2), patch.object(
                continuation, 'load_helper', return_value=backup), patch.object(
                continuation, 'inventory', return_value=({1: row(1), 2: row(2)}, {})), patch.object(
                continuation, 'claim_slot') as claim, patch.object(continuation, 'collect') as collect:
            result = continuation.run(pathlib.Path(directory))
            self.assertEqual(result['status'], 'target_reached')
            claim.assert_not_called()
            collect.assert_not_called()

    def test_completed_slot_does_not_spend_requests_again(self):
        batch = continuation.slot_batch(dt.datetime.now(dt.timezone.utc))
        before = {'completed_batches': [{'batch': batch}], 'unfinished_batches': []}
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'load_helper'), patch.object(
                continuation, 'inventory', return_value=({1: row()}, before)), patch.object(
                continuation, 'claim_slot') as claim, patch.object(continuation, 'collect') as collect:
            result = continuation.run(pathlib.Path(directory), batch)
            self.assertEqual(result['status'], 'slot_complete')
            claim.assert_not_called()
            collect.assert_not_called()

    def test_new_worker_stops_if_a_completed_batch_already_found_a_patch_change(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'load_helper'), patch.object(
                continuation, 'inventory', return_value=({1: row()}, {'patch_changed': True})), patch.object(
                continuation, 'claim_slot') as claim, patch.object(continuation, 'collect') as collect:
            result = continuation.run(pathlib.Path(directory))
            self.assertEqual(result['status'], 'patch_changed')
            self.assertTrue(result['patch_changed'])
            claim.assert_not_called()
            collect.assert_not_called()

    def test_arbitrary_old_slot_cannot_issue_new_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'ROOT', pathlib.Path(directory)), patch.object(
                continuation, 'load_helper'), patch.object(continuation, 'inventory', return_value=({1: row()}, {'completed_batches': []})), patch.object(
                continuation, 'claim_slot') as claim:
            with self.assertRaises(continuation.ContinuationError):
                continuation.run(pathlib.Path(directory), 'ranked-official-20000101T00')
            claim.assert_not_called()

    def test_collect_caps_details_at_remaining_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'TARGET', 3), patch.object(
                continuation, 'DeduplicatingCollector') as collector:
            continuation.collect(pathlib.Path(directory), {1: row()})
            self.assertEqual(collector.call_args.args[0].detail_limit, 2)
            self.assertEqual(collector.call_args.args[0].request_limit, 675)

    def test_interrupted_request_stays_charged_without_fabricating_a_response(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(output=directory, source='ranked', detail_limit=600, rpm=40,
                                      daily_budget=675, daily_reserve=300, request_limit=675,
                                      min_duration=720, min_age_hours=2, max_age_days=7, max_list_pages=6)
            collector = continuation.DeduplicatingCollector(args, {})
            collector.db.execute('INSERT INTO requests(started_at,kind,match_id,url) VALUES (?,?,?,?)',
                                 ('2026-11-13T09:00:00+00:00', 'detail', 1, 'https://api.opendota.com/api/matches/1?meta=1'))
            collector.db.commit()
            collector.db.close()
            collector.lock.close()
            with patch.object(continuation.DeduplicatingCollector, 'run'):
                continuation.collect(pathlib.Path(directory), {})
            with sqlite3.connect(pathlib.Path(directory) / 'state.sqlite3') as db:
                records = db.execute('SELECT status,error FROM requests').fetchall()
            self.assertEqual(records, [(None, 'interrupted_before_response_recorded')])

    def test_stopped_partial_slot_is_sealed_instead_of_spending_another_budget(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'DeduplicatingCollector') as collector:
            source = pathlib.Path(directory)
            (source / 'report.json').write_text(json.dumps({'status': 'paused', 'reason': 'api_rate_limited'}))
            continuation.collect(source, {})
            collector.assert_not_called()

    def test_already_verified_ids_are_removed_before_detail_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(output=directory, source='ranked', detail_limit=600, rpm=40,
                                      daily_budget=675, daily_reserve=300, request_limit=675,
                                      min_duration=720, min_age_hours=2, max_age_days=7, max_list_pages=6)
            collector = continuation.DeduplicatingCollector(args, {1: row()})
            try:
                with patch.object(collector, 'request', return_value=({'rows': [{'match_id': 1}, {'match_id': 2}]}, {})):
                    collector.discover()
                self.assertEqual([r[0] for r in collector.db.execute('SELECT match_id FROM candidates')], [2])
                self.assertEqual(collector.get_meta('summary_filters')['already_in_verified_training'], 1)
            finally:
                collector.db.close()
                collector.lock.close()

    def test_lost_local_state_cannot_reuse_a_claimed_slot(self):
        backup = Mock()
        backup.batch_prefix.return_value = 'dota_onedrive:DOTA2_hero_selector/opendota/ranked-official-20261113T06'
        backup.read_remote.side_effect = lambda config, remote, output, *a: output.write(b'{"claim_id":"other"}')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(continuation.ContinuationError, 'slot_already_claimed'):
                continuation.claim_slot(backup, None, 'ranked-official-20261113T06', pathlib.Path(directory))
        backup.run_rclone.assert_not_called()

    def test_missing_original_or_changed_manifest_blocks_count_reset(self):
        for dirs in ([], [{'Name': continuation.ORIGINAL, 'IsDir': True}]):
            backup = Mock()
            backup.run_rclone.return_value = json.dumps(dirs).encode()
            backup.load_manifest.return_value = ({}, b'', 'wrong-sha')
            with self.subTest(dirs=dirs), self.assertRaises(continuation.ContinuationError):
                continuation.inventory(backup, None, pathlib.Path('/unused'))

    def test_inventory_filters_storage_test_copies_and_checks_real_row_counts(self):
        backup = Mock()
        backup.run_rclone.return_value = json.dumps([
            {'Name': continuation.ORIGINAL, 'IsDir': True},
            {'Name': 'published-check-20261113T151026180709', 'IsDir': True},
            {'Name': 'storage-e2e-20261113T054634Z-singlepart', 'IsDir': True}]).encode()
        backup.load_manifest.return_value = ({}, b'', continuation.ORIGINAL_MANIFEST_SHA)
        backup.verify_batch.return_value = {'passed': True, 'manifest_sha256': continuation.ORIGINAL_MANIFEST_SHA}
        dataset = (json.dumps(row()) + '\n').encode()
        reports = {'matches.jsonl': dataset, 'report.json': json.dumps({
            'dataset_sha256': hashlib.sha256(dataset).hexdigest(), 'accepted_matches': 1,
            'integrity_errors': []}).encode(), 'audit.json': json.dumps({
            'passed': True, 'training_matches_checked': 1}).encode()}
        with patch.object(continuation, 'checked_artifact', side_effect=lambda b, c, batch, manifest, name, cache: reports[name]):
            rows, report = continuation.inventory(backup, None, pathlib.Path('/unused'))
        self.assertEqual(set(rows), {1})
        backup.load_manifest.assert_called_once()
        self.assertEqual(len(report['completed_batches']), 1)
        backup.verify_batch.assert_called_once_with(None, continuation.ORIGINAL, read_method=continuation.READ_METHOD)

    def test_remote_readback_failure_does_not_commit_new_matches(self):
        backup = Mock()
        backup.verify_batch.side_effect = RuntimeError('offline readback failure')
        with tempfile.TemporaryDirectory() as directory, patch.object(continuation, 'load_helper', return_value=backup), patch.object(
                continuation, 'inventory', return_value=({1: row()}, {'completed_batches': []})) as inventory, patch.object(
                continuation, 'claim_slot'), patch.object(continuation, 'collect'), patch.object(
                continuation, 'prepare', return_value=(pathlib.Path(directory), pathlib.Path(directory) / 'raw.tar.zst')):
            with self.assertRaises(RuntimeError):
                continuation.run(pathlib.Path(directory))
            self.assertEqual(inventory.call_count, 1)
            self.assertFalse((pathlib.Path(directory) / 'latest-result.json').exists())


class ContinuationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.contexts = contextlib.ExitStack()
        self.addCleanup(self.contexts.close)
        self.root = pathlib.Path(self.contexts.enter_context(tempfile.TemporaryDirectory()))
        self.remote, self.writes, self.reads = self.contexts.enter_context(memory_remote())
        self.contexts.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.config, self.archive, self.training = local_batch(self.root)
        self.batch = 'ranked-official-20261113T06'
        self.work = self.root / 'work'
        self.write_training([row(1)])
        backup.upload_batch(self.config, self.archive, self.training, continuation.ORIGINAL,
                            self.root / 'original-upload.json', continuation.READ_METHOD)
        original_manifest = self.remote[backup.batch_prefix(continuation.ORIGINAL) + '/manifest.json']
        self.write_training([row(1), row(2)])
        (self.training / 'collection-summary.json').write_text(json.dumps({'patch_changed': False}))

        remote_read = backup.read_remote.side_effect
        remote_write = backup.run_rclone.side_effect

        def read(config, path, *args, **kwargs):
            if path not in self.remote:
                raise backup.RemoteMissing('offline fixture: missing')
            return remote_read(config, path, *args, **kwargs)

        def rclone(config, operation, *args):
            if operation == 'lsjson':
                self.assertEqual(args, (continuation.REMOTE_ROOT, '--dirs-only'))
                names = {path[len(continuation.REMOTE_ROOT) + 1:].split('/')[0] for path in self.remote}
                return json.dumps([{'Name': name, 'IsDir': True} for name in names]).encode()
            return remote_write(config, operation, *args)

        backup.read_remote.side_effect = read
        backup.run_rclone.side_effect = rclone
        for patcher in (patch.object(continuation, 'ORIGINAL_MANIFEST_SHA', hashlib.sha256(original_manifest).hexdigest()),
                        patch.object(continuation, 'load_helper', return_value=backup),
                        patch.object(backup, 'restore_config', return_value=self.config),
                        patch.object(continuation, 'ROOT', self.root),
                        patch.object(continuation, 'slot_batch', return_value=self.batch)):
            self.contexts.enter_context(patcher)

    def write_training(self, rows):
        dataset = ''.join(json.dumps(item) + '\n' for item in rows).encode()
        (self.training / 'matches.jsonl').write_bytes(dataset)
        (self.training / 'report.json').write_text(json.dumps({
            'dataset_sha256': hashlib.sha256(dataset).hexdigest(), 'accepted_matches': len(rows),
            'integrity_errors': []}))
        (self.training / 'audit.json').write_text(json.dumps({'passed': True, 'training_matches_checked': len(rows)}))

    def publish_batch(self):
        backup.upload_batch(self.config, self.archive, self.training, self.batch,
                            self.root / 'upload.json', continuation.READ_METHOD)

    def test_published_manifest_after_failed_verify_blocks_restart_before_count_or_collection(self):
        check_file = backup.check_file

        def fail_raw_read(config, prefix, entry, *args, **kwargs):
            if prefix == backup.batch_prefix(self.batch) and entry['path'].startswith('raw/'):
                raise backup.BackupError('offline independent readback failure')
            return check_file(config, prefix, entry, *args, **kwargs)

        with patch.object(continuation, 'claim_slot'), patch.object(continuation, 'collect') as collect, patch.object(
                continuation, 'prepare', return_value=(self.training, self.archive)), patch.object(
                backup, 'check_file', side_effect=fail_raw_read):
            with self.assertRaisesRegex(backup.BackupError, 'independent readback failure'):
                continuation.run(self.work, self.batch)
            collect.assert_called_once()
            self.assertIn(backup.batch_prefix(self.batch) + '/manifest.json', self.remote)
            writes_after_upload = list(self.writes)
            collect.reset_mock()
            with self.assertRaisesRegex(backup.BackupError, 'independent readback failure'):
                continuation.run(self.work, self.batch)
            collect.assert_not_called()
            self.assertEqual(self.writes, writes_after_upload)
            self.assertFalse((self.work / 'reports' / (self.batch + '-verify.json')).exists())

        # The next worker must retry verification, then count the shared match only once.
        with patch.object(continuation, 'claim_slot') as claim, patch.object(continuation, 'collect') as collect:
            result = continuation.run(self.root / 'fresh-work', self.batch)
        self.assertEqual(result['status'], 'slot_complete')
        self.assertEqual(result['verified_unique_matches'], 2)
        self.assertEqual(result['duplicate_rows_not_counted'], 1)
        claim.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(self.writes, writes_after_upload)

    def test_missing_or_corrupt_raw_archive_blocks_even_with_warm_training_cache(self):
        self.publish_batch()
        continuation.inventory(backup, self.config, self.work / 'cache')
        writes_after_upload = list(self.writes)
        for batch in (continuation.ORIGINAL, self.batch):
            raw_path = backup.batch_prefix(batch) + '/raw/' + self.archive.name
            original = self.remote[raw_path]
            for damage in ('missing', 'hash', 'size'):
                with self.subTest(batch=batch, damage=damage):
                    if damage == 'missing':
                        del self.remote[raw_path]
                    elif damage == 'hash':
                        self.remote[raw_path] = b'X' * len(original)
                    else:
                        self.remote[raw_path] = original + b'X'
                    try:
                        with patch.object(continuation, 'claim_slot') as claim, patch.object(
                                continuation, 'collect') as collect, self.assertRaises(backup.BackupError):
                            continuation.run(self.work, self.batch)
                        claim.assert_not_called()
                        collect.assert_not_called()
                        self.assertEqual(self.writes, writes_after_upload)
                    finally:
                        self.remote[raw_path] = original

    def test_successful_repeated_recovery_rechecks_every_manifest_file_without_writes(self):
        self.publish_batch()
        writes_after_upload = list(self.writes)
        for work in (self.work, self.work, self.root / 'fresh-work'):
            self.reads.clear()
            with patch.object(continuation, 'claim_slot') as claim, patch.object(continuation, 'collect') as collect:
                result = continuation.run(work, self.batch)
            self.assertEqual(result['status'], 'slot_complete')
            self.assertEqual(result['verified_unique_matches'], 2)
            self.assertEqual(result['duplicate_rows_not_counted'], 1)
            self.assertEqual(len(result['completed_batches']), 2)
            for batch in (continuation.ORIGINAL, self.batch):
                prefix = backup.batch_prefix(batch)
                manifest = json.loads(self.remote[prefix + '/manifest.json'])
                for entry in manifest['files']:
                    self.assertIn(prefix + '/' + entry['path'], self.reads)
            claim.assert_not_called()
            collect.assert_not_called()
            self.assertEqual(self.writes, writes_after_upload)

    def test_failed_or_different_manifest_verification_cannot_be_counted(self):
        self.publish_batch()
        for report in ({'passed': False, 'manifest_sha256': continuation.ORIGINAL_MANIFEST_SHA},
                       {'passed': True, 'manifest_sha256': 'different-manifest'}):
            with self.subTest(report=report), patch.object(backup, 'verify_batch', return_value=report), self.assertRaises(
                    continuation.ContinuationError):
                continuation.inventory(backup, self.config, self.work / 'cache')


if __name__ == '__main__':
    unittest.main()
