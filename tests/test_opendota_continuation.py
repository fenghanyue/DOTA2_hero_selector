import argparse
import copy
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import continue_opendota_collection as continuation


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


if __name__ == '__main__':
    unittest.main()
