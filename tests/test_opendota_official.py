import copy
import fcntl
import gzip
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from export_opendota_training import export_training
from test_opendota_collection import args, collector_module as collector, fixture


def official_fixture():
    raw, summary = fixture()
    raw['od_data']['has_parsed'] = False
    teams = []
    for side, slots in ((2, range(5)), (3, range(128, 133))):
        teams.append({'dota_team': side, 'players': [
            {'player_slot': slot, 'lane_selection_flags': flag}
            for slot, flag in zip(slots, (1, 4, 2, 8, 16))]})
    teams.extend({'dota_team': side, 'players': []} for side in range(6, 14))
    raw['metadata'] = {'data': {'match_id': '100', 'metadata': {'teams': teams}}, 'error': None}
    for player in raw['players']:
        player['leaver_status'] = 0
        player.pop('position_est')
    return raw, summary


class OfficialRolesTests(unittest.TestCase):
    def test_unparsed_match_with_ten_metadata_teams_has_all_official_roles(self):
        raw, summary = official_fixture()
        result = collector.normalize_match(raw, summary, 'official')
        self.assertTrue(result['match']['official_roles_complete'])
        self.assertFalse(result['match']['has_parsed'])
        self.assertEqual([p['position'] for p in result['players']], [1, 2, 3, 4, 5] * 2)
        self.assertTrue(all(p['position_est'] is None for p in result['players']))
        self.assertEqual({p['position_source'] for p in result['players']}, {collector.POSITION_SOURCE_OFFICIAL})

    def test_metadata_errors_never_fall_back_to_estimates(self):
        cases = ('missing', 'fetch_error', 'wrong_id', 'wrong_slots', 'duplicate_roles',
                 'multi_role', 'bool_flag', 'missing_flag', 'extra_players', 'duplicate_team', 'missing_team')
        for case in cases:
            raw, summary = official_fixture()
            for i, player in enumerate(raw['players']):
                player['position_est'] = i % 5 + 1
            teams = raw['metadata']['data']['metadata']['teams']
            if case == 'missing':
                raw['metadata'] = None
            elif case == 'fetch_error':
                raw['metadata']['error'] = 'not available'
            elif case == 'wrong_id':
                raw['metadata']['data']['match_id'] = '101'
            elif case == 'wrong_slots':
                teams[0]['players'][0]['player_slot'] = 128
            elif case == 'duplicate_roles':
                teams[0]['players'][0]['lane_selection_flags'] = 4
            elif case == 'multi_role':
                teams[0]['players'][0]['lane_selection_flags'] = 31
            elif case == 'bool_flag':
                teams[0]['players'][0]['lane_selection_flags'] = True
            elif case == 'missing_flag':
                del teams[0]['players'][0]['lane_selection_flags']
            elif case == 'extra_players':
                teams[2]['players'] = [{'player_slot': 1}]
            elif case == 'duplicate_team':
                teams.append(copy.deepcopy(teams[0]))
            else:
                del teams[0]
            with self.subTest(case=case):
                result = collector.normalize_match(raw, summary, 'official')
                self.assertFalse(result['match']['roles_complete'])
                self.assertTrue(result['match']['official_role_error'])
                self.assertTrue(all(p['position'] is None for p in result['players']))
                self.assertTrue(all(p['position_est'] is not None for p in result['players']))

    def test_plain_metadata_file_is_also_supported(self):
        raw, _ = official_fixture()
        raw['metadata'] = raw['metadata']['data']
        positions, error = collector.official_positions(raw)
        self.assertIsNone(error)
        self.assertEqual(len(positions), 10)

    def test_wrong_hero_or_winner_is_rejected_even_with_valid_flags(self):
        raw, summary = official_fixture()
        raw['players'][1]['hero_id'] = raw['players'][0]['hero_id']
        self.assertFalse(collector.normalize_match(raw, summary, 'official')['match']['roles_complete'])
        raw, summary = official_fixture()
        raw['radiant_win'] = 0
        self.assertFalse(collector.normalize_match(raw, summary, 'official')['match']['basic_ok'])


class RankedPersistenceTests(unittest.TestCase):
    def make_collector(self, directory, **overrides):
        c = collector.Collector(args(directory, source='ranked', **overrides))
        self.addCleanup(c.lock.close)
        self.addCleanup(c.db.close)
        return c

    def test_explorer_pages_only_ranked_ids_and_advances(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory)
            _, summary = official_fixture()
            c.request = Mock(return_value=({'rows': [summary, summary], 'err': None}, {}))
            c.discover()
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0], 1)
            sql = urllib.parse.parse_qs(urllib.parse.urlsplit(c.request.call_args.args[0]).query)['sql'][0]
            self.assertIn('lobby_type=7 AND game_mode=22 AND duration>=720', sql)
            self.assertNotIn('match_id<', sql)
            c.request = Mock(return_value=({'rows': [dict(summary, match_id=99)], 'err': None}, {}))
            c.discover()
            sql = urllib.parse.parse_qs(urllib.parse.urlsplit(c.request.call_args.args[0]).query)['sql'][0]
            self.assertIn('match_id<100', sql)
            self.assertEqual(c.get_meta('cursor'), 99)

    def test_different_duration_cannot_reuse_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory)
            c.db.close()
            c.lock.close()
            with self.assertRaisesRegex(ValueError, 'incompatible cohort'):
                collector.Collector(args(directory, source='ranked', min_duration=900))

    def test_total_budget_caps_retries_before_third_request(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory, request_limit=2)
            def fail(*a, **kw):
                raise urllib.error.HTTPError('https://example.invalid', 500, 'error', {}, io.BytesIO(b'failed'))
            with patch.object(collector.urllib.request, 'urlopen', side_effect=fail) as network, patch.object(collector.time, 'sleep'):
                with self.assertRaisesRegex(collector.PauseCollection, 'total_request_budget'):
                    c.request('/matches/100?meta=1', 'detail', 100)
            self.assertEqual(network.call_count, 2)
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 2)

    def create_detail(self, directory, raw=None):
        c = self.make_collector(directory)
        default_raw, summary = official_fixture()
        raw = default_raw if raw is None else raw
        c.db.execute('INSERT INTO candidates(match_id,summary) VALUES (?,?)', (100, json.dumps(summary)))
        c.db.commit()
        response = io.BytesIO(json.dumps(raw).encode())
        response.status = 200
        response.headers = {'x-rate-limit-remaining-day': '2999'}
        with patch.object(collector.urllib.request, 'urlopen', return_value=response) as network:
            c.collect_detail(c.db.execute('SELECT * FROM candidates').fetchone())
            self.assertTrue(network.call_args.args[0].full_url.endswith('/matches/100?meta=1'))
        c.export('detail_trial_complete')
        c.db.close()
        c.lock.close()
        return c

    def test_export_checks_provenance_and_preserves_false_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = pathlib.Path(directory) / 'source', pathlib.Path(directory) / 'output'
            self.create_detail(source)
            report = export_training(source, output, 60)
            self.assertEqual(report['accepted_matches'], 1)
            row = json.loads((output / 'matches.jsonl').read_text())
            self.assertIs(row['radiant_win'], False)
            self.assertEqual(row['radiant'], [1, 2, 3, 4, 5])
            self.assertEqual(report['dataset_sha256'], hashlib.sha256((output / 'matches.jsonl').read_bytes()).hexdigest())
            provenance_file = source / 'raw/matches/100.source.json'
            provenance = json.loads(provenance_file.read_text())
            provenance['sha256'] = 'bad'
            provenance_file.write_text(json.dumps(provenance))
            report = export_training(source, output, 60)
            self.assertEqual(len(report['integrity_errors']), 1)
            self.assertEqual((output / 'matches.jsonl').read_bytes(), b'')

    def test_leaver_and_patch_screening_and_zero_output(self):
        for changed in ('leaver', 'patch', 'missing_leaver', 'short'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                raw, _ = official_fixture()
                if changed == 'leaver':
                    raw['players'][0]['leaver_status'] = 2
                elif changed == 'missing_leaver':
                    del raw['players'][0]['leaver_status']
                elif changed == 'patch':
                    raw['patch'] = 61
                else:
                    raw['duration'] = 719
                source, output = pathlib.Path(directory) / 'source', pathlib.Path(directory) / 'output'
                self.create_detail(source, raw)
                report = export_training(source, output, 60)
                self.assertEqual(report['accepted_matches'], 0)
                self.assertEqual(report['rejected_matches'], 1)
                self.assertFalse(report['integrity_errors'])

    def test_export_refuses_active_collector_and_in_place_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = pathlib.Path(directory) / 'source', pathlib.Path(directory) / 'output'
            c = self.make_collector(source)
            with self.assertRaisesRegex(RuntimeError, 'stop the collector'):
                export_training(source, output, 60)
            with self.assertRaisesRegex(ValueError, 'outside'):
                export_training(source, source / 'export', 60)


if __name__ == '__main__':
    unittest.main()
