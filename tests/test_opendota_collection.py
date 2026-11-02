import argparse
import copy
import datetime as dt
import gzip
import importlib.util
import io
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'scripts/collect_opendota.py'
spec = importlib.util.spec_from_file_location('collect_opendota', MODULE_PATH)
collector_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector_module)

AUDIT_PATH = MODULE_PATH.with_name('analyze_opendota_parsed_pilot.py')
audit_spec = importlib.util.spec_from_file_location('analyze_opendota_parsed_pilot', AUDIT_PATH)
audit_module = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(audit_module)


def fixture():
    start = int(time.time()) - 14400
    summary = {'match_id': 100, 'start_time': start, 'duration': 1800,
               'game_mode': 22, 'lobby_type': 7, 'avg_rank_tier': 50, 'num_rank_tier': 8}
    players = []
    for index in range(10):
        role = index % 5 + 1
        players.append({
            'hero_id': index + 1, 'player_slot': index if index < 5 else index + 123,
            'position_est': role, 'lane': 2 if role == 2 else 1 if role in (1, 5) else 3,
            'lane_role': 2 if role == 2 else 1 if role in (1, 5) else 3,
            'is_roaming': False, 'times': [0, 120, 360, 420],
            'networth_t': [600, 1000, 2500, 3000], 'xp_t': [0, 200, 1000, 1200],
            'lh_t': [0, 5, 20, 30], 'dn_t': [0, 0, 1, 2],
            'kills_log': [{'time': x} for x in [-5, 0, 359, 419, 420]], 'deaths_log': [],
        })
    match = {'match_id': 100, 'start_time': start, 'duration': 1800, 'patch': 60,
             'game_mode': 22, 'lobby_type': 7, 'radiant_win': False,
             'version': 22, 'od_data': {'has_parsed': True}, 'players': players}
    return match, summary


def args(output, **overrides):
    values = dict(output=output, detail_limit=1, rpm=50, daily_budget=2700,
                  daily_reserve=100, min_age_hours=2, max_age_days=7, max_list_pages=1000)
    values.update(overrides)
    return argparse.Namespace(**values)


class NormalizationTests(unittest.TestCase):
    def test_match_rank_uses_known_medals_and_preserves_training_eligibility(self):
        match, summary = fixture()
        tiers = [None, 61, 80, None, None, 75, None, None, None, 80]
        for player, tier in zip(match['players'], tiers):
            player['rank_tier'] = tier
        result = collector_module.normalize_match(match, summary)['match']
        self.assertEqual(result['rank_band_est'], '超凡入圣')
        self.assertEqual(result['rank_known_players'], 4)
        self.assertTrue(result['position_training_ready'])
        for player in match['players']:
            player.pop('rank_tier')
        result = collector_module.normalize_match(match, summary)['match']
        self.assertIsNone(result['rank_band_est'])
        self.assertEqual(result['rank_known_players'], 0)
        self.assertTrue(result['position_training_ready'])

    def test_rank_estimation_ignores_stars_invalid_medals_and_boundary_gaps(self):
        cases = [
            ([71, 75, 80], ('超凡入圣', 3)),
            ([45, 51], ('统帅', 2)),
            ([51, 55, 61, 64], ('传奇', 4)),
            ([80, None, 0, 10, 16, 70, 81, 90, True, 80.0, '80'], ('冠绝一世', 1)),
            ([None, 0, 10, 81, '80'], (None, 0)),
            ([], (None, 0)),
        ]
        for tiers, expected in cases:
            with self.subTest(tiers=tiers):
                players = [{'rank_tier': tier} for tier in tiers]
                self.assertEqual(collector_module.estimate_rank_band(players), expected)

    def test_false_winner_and_zero_metrics_are_real_values(self):
        match, summary = fixture()
        match['players'][0]['networth_t'][-1] = 0
        normalized = collector_module.normalize_match(match, summary)
        self.assertIs(normalized['match']['radiant_win'], False)
        self.assertTrue(normalized['match']['basic_ok'])
        self.assertTrue(normalized['match']['position_training_ready'])
        self.assertTrue(normalized['match']['early_420_complete'])
        self.assertEqual(normalized['players'][0]['networth_420'], 0)

    def test_sample_alignment_uses_times_not_array_index(self):
        match, summary = fixture()
        p = collector_module.normalize_match(match, summary)['players'][0]
        self.assertEqual(p['networth_360'], 2500)
        self.assertEqual(p['networth_420'], 3000)
        match['players'][0]['xp_t'].pop()
        result = collector_module.normalize_match(match, summary)
        self.assertIsNone(result['players'][0]['xp_420'])
        self.assertFalse(result['match']['early_420_complete'])

    def test_missing_data_is_not_fabricated_as_zero_or_role(self):
        match, summary = fixture()
        for key in ['position_est', 'lane', 'lane_role', 'is_roaming', 'times', 'networth_t', 'xp_t', 'lh_t', 'kills_log', 'deaths_log']:
            match['players'][0].pop(key)
        result = collector_module.normalize_match(match, summary)
        self.assertTrue(result['match']['basic_ok'])
        self.assertFalse(result['match']['roles_complete'])
        self.assertFalse(result['match']['lane_complete'])
        self.assertFalse(result['match']['events_before_420_complete'])
        for key in ['position_est', 'position_source', 'xp_420', 'kills_before_420', 'deaths_before_420']:
            self.assertIsNone(result['players'][0][key])

    def test_event_windows_exclude_pregame_and_cutoff(self):
        match, summary = fixture()
        p = collector_module.normalize_match(match, summary)['players'][0]
        self.assertEqual(p['kills_before_360'], 2)
        self.assertEqual(p['kills_before_420'], 3)
        self.assertEqual(p['deaths_before_420'], 0)

    def test_duplicate_roles_and_duplicate_heroes_are_flagged(self):
        match, summary = fixture()
        match['players'][1]['position_est'] = 1
        self.assertFalse(collector_module.normalize_match(match, summary)['match']['roles_complete'])
        match['players'][1]['hero_id'] = match['players'][0]['hero_id']
        self.assertFalse(collector_module.normalize_match(match, summary)['match']['basic_ok'])

    def test_details_must_match_the_requested_id_and_mode(self):
        match, summary = fixture()
        match['match_id'] = 101
        with self.assertRaises(ValueError):
            collector_module.normalize_match(match, summary)
        match['match_id'] = 100
        match['game_mode'] = 23
        self.assertFalse(collector_module.normalize_match(match, summary)['match']['basic_ok'])

    def test_summary_selection_does_not_depend_on_parse_or_roles(self):
        _, summary = fixture()
        now = int(time.time())
        self.assertIsNone(collector_module.summary_filter(summary, now - 7 * 86400, now - 7200))
        summary['game_mode'] = 23
        self.assertEqual(collector_module.summary_filter(summary, now - 7 * 86400, now - 7200), 'other_mode_or_lobby')
        summary['game_mode'] = 22
        summary['start_time'] = now - 60
        self.assertEqual(collector_module.summary_filter(summary, now - 7 * 86400, now - 7200), 'too_recent')

    def test_quality_screen_does_not_promote_wrong_modes_patches_or_abandoned_matches(self):
        original, _ = fixture()
        for player in original['players']:
            player['leaver_status'] = 0
        self.assertTrue(audit_module.raw_checks(original, 60)['screened'])
        for change in ('turbo', 'other_patch', 'short_match', 'abandoned', 'unknown_leaver'):
            match = copy.deepcopy(original)
            if change == 'turbo':
                match.update(game_mode=23, lobby_type=0)
            elif change == 'other_patch':
                match['patch'] = 61
            elif change == 'short_match':
                match['duration'] = 560
            elif change == 'abandoned':
                match['players'][0]['leaver_status'] = 2
            else:
                del match['players'][0]['leaver_status']
            with self.subTest(change=change):
                self.assertFalse(audit_module.raw_checks(match, 60)['screened'])


class PersistenceTests(unittest.TestCase):
    def make_collector(self, directory, **overrides):
        c = collector_module.Collector(args(directory, **overrides))
        self.addCleanup(c.lock.close)
        self.addCleanup(c.db.close)
        return c

    def test_discovery_deduplicates_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory)
            _, summary = fixture()
            c.request = Mock(return_value=([summary, summary], {}))
            c.discover()
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0], 1)
            older = dict(summary, match_id=99)
            c.request = Mock(return_value=([summary, older], {}))
            c.discover()
            self.assertEqual(c.get_meta('cursor'), 99)
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0], 2)

    def test_parsed_discovery_accepts_id_only_rows_and_pages_without_summary_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory, source='parsed')
            c.request = Mock(return_value=([{'match_id': 100}, {'match_id': 100}, None,
                                            {'match_id': 0}, {'match_id': True}], {}))
            c.discover()
            c.request.assert_called_once_with('/parsedMatches', 'list')
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0], 1)
            self.assertIsNone(c.config['earliest_start'])
            self.assertIsNone(c.config['latest_end'])
            self.assertEqual(c.get_meta('summary_filters')['invalid_match_id'], 3)
            c.request = Mock(return_value=([{'match_id': 99}], {}))
            c.discover()
            c.request.assert_called_once_with('/parsedMatches?less_than_match_id=100', 'list')
            self.assertEqual(c.get_meta('cursor'), 99)

    def test_public_and_parsed_cohorts_cannot_share_an_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_collector(directory)
            original.db.close()
            original.lock.close()
            with self.assertRaisesRegex(ValueError, 'incompatible cohort'):
                collector_module.Collector(args(directory, source='parsed'))

    def test_parsed_trial_stops_at_id_limit_instead_of_filling_target_mode_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory, source='parsed', detail_limit=1)
            match, _ = fixture()
            match['game_mode'] = 23
            path = pathlib.Path(directory) / 'raw/matches/100.json.gz'
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(json.dumps(match).encode()))
            c.request = Mock(return_value=([{'match_id': 100}, {'match_id': 99}], {}))
            self.assertEqual(c.run(), 0)
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM candidates WHERE attempted=1').fetchone()[0], 1)
            c.request.assert_called_once_with('/parsedMatches', 'list')
            self.assertFalse(c.results()[0]['match']['basic_ok'])

    def test_cached_detail_resumes_without_another_http_request(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory)
            match, summary = fixture()
            c.db.execute('INSERT INTO candidates(match_id,summary) VALUES (?,?)', (100, json.dumps(summary)))
            c.db.commit()
            path = pathlib.Path(directory) / 'raw/matches/100.json.gz'
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(json.dumps(match).encode()))
            c.request = Mock(side_effect=AssertionError('cache must avoid HTTP'))
            self.assertEqual(c.run(), 0)
            self.assertEqual(c.db.execute('SELECT status FROM candidates').fetchone()[0], 'ok')
            self.assertTrue((pathlib.Path(directory) / 'matches.csv').is_file())

    def test_daily_budget_stops_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory, daily_budget=1)
            c.db.execute('INSERT INTO requests(started_at,kind,url) VALUES (?,?,?)',
                         (collector_module.utc_now(), 'list', 'https://example.invalid'))
            c.db.commit()
            with patch.object(collector_module.urllib.request, 'urlopen') as network:
                with self.assertRaisesRegex(collector_module.PauseCollection, 'local_daily'):
                    c.request('/publicMatches', 'list')
                network.assert_not_called()

    def test_yesterdays_empty_quota_does_not_permanently_block_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.make_collector(directory)
            c.set_meta('last_response_headers', {'x-rate-limit-remaining-day': '0'})
            yesterday = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
            c.set_meta('last_response_at_utc', yesterday.isoformat())
            response = io.BytesIO(b'[]')
            response.status = 200
            response.headers = {'x-rate-limit-remaining-day': '2999'}
            with patch.object(collector_module.urllib.request, 'urlopen', return_value=response) as network:
                payload, _ = c.request('/publicMatches', 'list')
                self.assertEqual(payload, [])
                network.assert_called_once()


if __name__ == '__main__':
    unittest.main()
