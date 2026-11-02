#!/usr/bin/env python3
"""Collect bounded match details from public summaries or already-parsed IDs."""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = 'https://api.opendota.com/api'
TARGET_MODE = 22
TARGET_LOBBY = 7
RANK_BANDS = ('先锋', '卫士', '中军', '统帅', '传奇', '万古流芳', '超凡入圣', '冠绝一世')


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value):
    return type(value) is int


def estimate_rank_band(players):
    """Estimate the lobby medal from known profiles, with stars ignored."""
    bands = []
    for player in players:
        tier = player.get('rank_tier')
        if not integer(tier):
            continue
        medal, stars = divmod(tier, 10)
        if tier == 80 or (1 <= medal <= 7 and 1 <= stars <= 5):
            bands.append(medal)
    bands.sort()
    # Use the lower median so an even split does not invent an in-between tier.
    band = RANK_BANDS[bands[(len(bands) - 1) // 2] - 1] if bands else None
    return band, len(bands)


def atomic_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(payload)
    temp.replace(path)


def write_json(path, payload):
    atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode())


def summary_filter(row, earliest_start, latest_end):
    if not isinstance(row, dict):
        return 'invalid_summary'
    if row.get('game_mode') != TARGET_MODE or row.get('lobby_type') != TARGET_LOBBY:
        return 'other_mode_or_lobby'
    if not integer(row.get('match_id')) or row['match_id'] <= 0:
        return 'invalid_match_id'
    start, duration = row.get('start_time'), row.get('duration')
    if not integer(start) or not integer(duration) or duration < 0:
        return 'invalid_time'
    if start < earliest_start:
        return 'outside_age_window'
    if start + duration > latest_end:
        return 'too_recent'
    # Do not preselect for parsed data, position availability, or long duration:
    # those are exactly the coverage outcomes this pilot needs to measure.
    return None


def sample_value(player, field, seconds):
    times, values = player.get('times'), player.get(field)
    if not isinstance(times, list) or not isinstance(values, list):
        return None
    if len(times) != len(values) or times.count(seconds) != 1:
        return None
    value = values[times.index(seconds)]
    return value if finite_number(value) else None


def event_count(player, field, seconds):
    events = player.get(field)
    if not isinstance(events, list):
        return None
    if any(not isinstance(e, dict) or not finite_number(e.get('time')) for e in events):
        return None
    return sum(0 <= e['time'] < seconds for e in events)


def normalize_match(data, summary):
    if not isinstance(data, dict) or data.get('match_id') != summary['match_id']:
        raise ValueError('response match_id does not match requested match')
    players = data.get('players')
    players = players if isinstance(players, list) else []
    players = [p for p in players if isinstance(p, dict)]
    slots = [p.get('player_slot') for p in players]
    heroes = [p.get('hero_id') for p in players]
    expected_slots = set(range(5)) | set(range(128, 133))
    valid_slots = len(slots) == 10 and all(integer(s) for s in slots) and set(slots) == expected_slots
    valid_heroes = len(heroes) == 10 and all(integer(h) and h > 0 for h in heroes) and len(set(heroes)) == 10
    valid_winner = type(data.get('radiant_win')) is bool
    target_match = data.get('game_mode') == TARGET_MODE and data.get('lobby_type') == TARGET_LOBBY
    valid_context = integer(data.get('start_time')) and integer(data.get('patch')) and integer(data.get('duration')) and data['duration'] >= 0
    basic_ok = bool(valid_slots and valid_heroes and valid_winner and target_match and valid_context)
    normalized_players = []
    for p in players:
        slot = p.get('player_slot')
        side = ('radiant' if slot < 128 else 'dire') if integer(slot) else None
        role = p.get('position_est')
        role_valid = integer(role) and role in range(1, 6)
        row = {
            'match_id': data['match_id'], 'player_slot': slot, 'side': side,
            'hero_id': p.get('hero_id'), 'position_est': role if role_valid else None,
            'position_source': 'opendota_position_est' if role_valid else None,
            'lane': p.get('lane'), 'lane_role': p.get('lane_role'),
            'is_roaming': p.get('is_roaming'),
        }
        for seconds in (360, 420):
            for field, label in [('networth_t', 'networth'), ('gold_t', 'earned_gold'),
                                 ('xp_t', 'xp'), ('lh_t', 'last_hits'), ('dn_t', 'denies')]:
                row[f'{label}_{seconds}'] = sample_value(p, field, seconds)
            row[f'kills_before_{seconds}'] = event_count(p, 'kills_log', seconds)
            row[f'deaths_before_{seconds}'] = event_count(p, 'deaths_log', seconds)
        normalized_players.append(row)
    roles_complete = basic_ok and all(
        {p['position_est'] for p in normalized_players if p['side'] == side} == set(range(1, 6))
        for side in ('radiant', 'dire')
    )
    lane_complete = basic_ok and all(
        integer(p['lane']) and p['lane'] in range(1, 6)
        and integer(p['lane_role']) and p['lane_role'] in range(1, 5)
        and type(p['is_roaming']) is bool for p in normalized_players
    )
    early_complete = {}
    for seconds in (360, 420):
        early_complete[seconds] = basic_ok and all(
            all(finite_number(p[f'{field}_{seconds}']) for field in ('networth', 'xp', 'last_hits'))
            for p in normalized_players
        )
    event_complete = basic_ok and all(p['kills_before_420'] is not None and p['deaths_before_420'] is not None for p in normalized_players)
    dual_lanes = []
    if lane_complete:
        for lane in (1, 3):
            sides = [[p for p in normalized_players if p['lane'] == lane and p['side'] == side] for side in ('radiant', 'dire')]
            if all(len(group) == 2 and not any(p['is_roaming'] for p in group) for group in sides):
                dual_lanes.append(lane)
    od = data.get('od_data') or {}
    rank_band, rank_known_players = estimate_rank_band(players)
    match_row = {
        'match_id': data['match_id'], 'start_time': data.get('start_time'),
        'patch': data.get('patch'), 'game_mode': data.get('game_mode'),
        'lobby_type': data.get('lobby_type'), 'duration': data.get('duration'),
        'radiant_win': data.get('radiant_win'),
        'avg_rank_tier_summary': summary.get('avg_rank_tier'),
        'num_rank_tier_summary': summary.get('num_rank_tier'),
        'rank_band_est': rank_band, 'rank_known_players': rank_known_players,
        'parse_version': data.get('version'), 'has_parsed': od.get('has_parsed'),
        'basic_ok': basic_ok, 'roles_complete': bool(roles_complete),
        'lane_complete': bool(lane_complete),
        'early_360_complete': bool(early_complete[360]),
        'early_420_complete': bool(early_complete[420]),
        'events_before_420_complete': bool(event_complete),
        'estimated_two_vs_two_lanes': len(dual_lanes),
        'position_training_ready': bool(basic_ok and roles_complete),
        'laning_metrics_ready': bool(basic_ok and lane_complete and roles_complete and early_complete[420]),
    }
    return {'match': match_row, 'players': normalized_players}


class PauseCollection(Exception):
    pass


class Collector:
    def __init__(self, args):
        self.args = args
        self.source = getattr(args, 'source', 'public')
        self.output = pathlib.Path(args.output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = (self.output / '.collector.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another collector is using this output directory') from exc
        self.db = sqlite3.connect(self.output / 'state.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidates (
                match_id INTEGER PRIMARY KEY, summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempted INTEGER NOT NULL DEFAULT 0,
                normalized TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, kind TEXT NOT NULL,
                match_id INTEGER, url TEXT NOT NULL, status INTEGER,
                headers TEXT, error TEXT, raw_path TEXT, sha256 TEXT);
        ''')
        config = self.get_meta('config')
        if config is None:
            now = time.time()
            config = {
                'source': self.source,
                'created_at': utc_now(), 'api_base': API, 'game_mode': TARGET_MODE,
                'lobby_type': TARGET_LOBBY,
                'min_completed_age_hours': args.min_age_hours if self.source == 'public' else None,
                'max_start_age_days': args.max_age_days if self.source == 'public' else None,
                'earliest_start': int(now - args.max_age_days * 86400) if self.source == 'public' else None,
                'latest_end': int(now - args.min_age_hours * 3600) if self.source == 'public' else None,
                'sampling_frame': (
                    'publicMatches summaries filtered before detail requests; not preselected for parsing'
                    if self.source == 'public' else
                    'parsedMatches IDs; all modes and ages fetched, ranked all-pick evaluated in details'),
                'daily_request_budget': args.daily_budget, 'daily_reserve': args.daily_reserve,
                'requests_per_minute': args.rpm,
            }
            self.set_meta('config', config)
            write_json(self.output / 'config.json', config)
        if (config['game_mode'] != TARGET_MODE or config['lobby_type'] != TARGET_LOBBY
                or config.get('source', 'public') != self.source):
            self.db.close()
            self.lock.close()
            raise ValueError('output directory contains an incompatible cohort')
        self.config = config
        self.next_request = 0.0
        self.last_export = time.monotonic()
        self.started = time.monotonic()
        self.set_meta('detail_limit', args.detail_limit)

    def get_meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def request(self, endpoint, kind, match_id=None):
        url = API + endpoint
        last_error = None
        for attempt in range(3):
            today = dt.datetime.now(dt.timezone.utc).date().isoformat()
            used = self.db.execute('SELECT COUNT(*) FROM requests WHERE substr(started_at,1,10)=?', (today,)).fetchone()[0]
            if used >= self.args.daily_budget:
                raise PauseCollection('local_daily_request_budget_reached')
            latest = self.get_meta('last_response_headers', {})
            remaining = latest.get('x-rate-limit-remaining-day')
            header_day = self.get_meta('last_response_at_utc', '')[:10]
            if header_day == today and remaining is not None and int(remaining) <= self.args.daily_reserve:
                raise PauseCollection('server_daily_quota_reserve_reached')
            delay = max(0.0, self.next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self.next_request = time.monotonic() + 60 / self.args.rpm
            request_id = self.db.execute(
                'INSERT INTO requests(started_at,kind,match_id,url) VALUES (?,?,?,?)',
                (utc_now(), kind, match_id, url)).lastrowid
            self.db.commit()
            status, body, headers, error = None, b'', {}, None
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'DOTA2-hero-selector-collector/0.1', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=25) as response:
                    status, body = response.status, response.read()
                    headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in
                               ['date', 'content-type', 'retry-after', 'x-rate-limit-remaining-minute', 'x-rate-limit-remaining-day']}
            except urllib.error.HTTPError as exc:
                status, body = exc.code, exc.read()
                headers = {k.lower(): v for k, v in exc.headers.items() if k.lower() in
                           ['date', 'retry-after', 'x-rate-limit-remaining-minute', 'x-rate-limit-remaining-day']}
                error = f'HTTP {status}'
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = str(exc)
            raw_path = None
            if body:
                raw_path = f'raw/http/{request_id:06d}-{kind}-{match_id or "page"}.json.gz'
                atomic_bytes(self.output / raw_path, gzip.compress(body, mtime=0))
            self.db.execute('UPDATE requests SET status=?,headers=?,error=?,raw_path=?,sha256=? WHERE id=?',
                            (status, json.dumps(headers), error, raw_path,
                             hashlib.sha256(body).hexdigest() if body else None, request_id))
            self.db.commit()
            if headers:
                self.set_meta('last_response_headers', headers)
                self.set_meta('last_response_at_utc', utc_now())
            if status == 200:
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError) as exc:
                    last_error = f'invalid_json: {exc}'
                else:
                    return payload, {'request_id': request_id, 'url': url, 'raw_path': raw_path,
                                     'sha256': hashlib.sha256(body).hexdigest(), 'headers': headers}
            else:
                last_error = error or f'HTTP {status}'
            if status in (401, 403):
                raise PauseCollection(f'api_access_rejected_http_{status}')
            if status == 429:
                if headers.get('x-rate-limit-remaining-day') == '0':
                    raise PauseCollection('server_daily_quota_exhausted')
                retry = headers.get('retry-after', '60')
                try:
                    retry_seconds = float(retry)
                except ValueError:
                    retry_seconds = 60.0
                if retry_seconds > 60 or attempt == 2:
                    raise PauseCollection('api_rate_limited')
                time.sleep(max(1, retry_seconds))
            elif status is not None and 400 <= status < 500:
                break
            elif attempt < 2:
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError(last_error or 'request failed')

    def discover(self):
        pages = self.get_meta('list_pages', 0)
        if pages >= self.args.max_list_pages:
            raise PauseCollection('max_list_pages_reached')
        cursor = self.get_meta('cursor')
        query = '?' + urllib.parse.urlencode({'less_than_match_id': cursor}) if cursor else ''
        endpoint = '/publicMatches' if self.source == 'public' else '/parsedMatches'
        payload, _ = self.request(endpoint + query, 'list')
        if not isinstance(payload, list) or not payload:
            raise PauseCollection(f'{self.source}_match_list_empty')
        ids = [x.get('match_id') for x in payload if isinstance(x, dict)
               and integer(x.get('match_id')) and x['match_id'] > 0]
        if not ids or (cursor is not None and min(ids) >= cursor):
            raise PauseCollection(f'nonadvancing_{self.source}_match_cursor')
        rejected = self.get_meta('summary_filters', {})
        newest = max((r['start_time'] for r in payload if isinstance(r, dict) and integer(r.get('start_time'))), default=2 ** 63)
        selected = 0
        for row in payload:
            if self.source == 'public':
                reason = summary_filter(row, self.config['earliest_start'], self.config['latest_end'])
            else:
                reason = (None if isinstance(row, dict) and integer(row.get('match_id'))
                          and row['match_id'] > 0 else 'invalid_match_id')
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                self.db.execute('INSERT OR IGNORE INTO candidates(match_id,summary) VALUES (?,?)',
                                (row['match_id'], json.dumps(row, ensure_ascii=False)))
                selected += 1
        self.db.commit()
        self.set_meta('summary_filters', rejected)
        self.set_meta('summaries_seen', self.get_meta('summaries_seen', 0) + len(payload))
        self.set_meta('list_pages', pages + 1)
        self.set_meta('cursor', min(ids))
        if self.source == 'public' and newest < self.config['earliest_start'] and selected == 0:
            raise PauseCollection('sampling_age_window_exhausted')

    def collect_detail(self, candidate):
        mid = candidate['match_id']
        summary = json.loads(candidate['summary'])
        self.db.execute('UPDATE candidates SET attempted=1 WHERE match_id=?', (mid,))
        self.db.commit()
        cached = self.output / f'raw/matches/{mid}.json.gz'
        try:
            if cached.exists():
                payload = json.loads(gzip.decompress(cached.read_bytes()))
            else:
                payload, provenance = self.request(f'/matches/{mid}', 'detail', mid)
                # Keep the exact response bytes, not a re-serialized approximation.
                source_path = self.output / provenance['raw_path']
                atomic_bytes(cached, source_path.read_bytes())
                write_json(self.output / f'raw/matches/{mid}.source.json', provenance)
            result = normalize_match(payload, summary)
            self.db.execute('UPDATE candidates SET status=?,normalized=?,error=NULL WHERE match_id=?',
                            ('ok', json.dumps(result, ensure_ascii=False), mid))
        except PauseCollection:
            raise
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            self.db.execute('UPDATE candidates SET status=?,error=? WHERE match_id=?', ('failed', str(exc), mid))
        self.db.commit()

    def results(self):
        rows = self.db.execute('SELECT normalized FROM candidates WHERE status="ok" ORDER BY match_id DESC').fetchall()
        return [json.loads(row[0]) for row in rows]

    def export(self, status, reason=None):
        results = self.results()
        fields = ['basic_ok', 'has_parsed', 'roles_complete', 'lane_complete', 'early_360_complete',
                  'early_420_complete', 'events_before_420_complete', 'position_training_ready', 'laning_metrics_ready']
        counts = {field: sum(r['match'][field] is True for r in results) for field in fields}
        stats = {
            'status': status, 'reason': reason, 'updated_at': utc_now(),
            'detail_limit': self.args.detail_limit,
            'unique_details_attempted': self.db.execute('SELECT COUNT(*) FROM candidates WHERE attempted=1').fetchone()[0],
            'successful_detail_responses': len(results),
            'failed_details': self.db.execute('SELECT COUNT(*) FROM candidates WHERE status="failed"').fetchone()[0],
            'pending_candidates': self.db.execute('SELECT COUNT(*) FROM candidates WHERE status="pending"').fetchone()[0],
            'list_pages': self.get_meta('list_pages', 0), 'summaries_seen': self.get_meta('summaries_seen', 0),
            'summary_filters': self.get_meta('summary_filters', {}),
            'http_requests': self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],
            'http_detail_requests': self.db.execute('SELECT COUNT(*) FROM requests WHERE kind="detail"').fetchone()[0],
            'http_list_requests': self.db.execute('SELECT COUNT(*) FROM requests WHERE kind="list"').fetchone()[0],
            'last_response_headers': self.get_meta('last_response_headers', {}),
            'quality_counts': counts,
            'rates_over_successful_details': {field: counts[field] / len(results) if results else None for field in fields},
            'patch_counts': dict(collections.Counter(str(r['match']['patch']) for r in results)),
            'detail_mode_counts': dict(collections.Counter(f'{r["match"]["game_mode"]}/{r["match"]["lobby_type"]}' for r in results)),
            'estimated_two_vs_two_lanes': sum(r['match']['estimated_two_vs_two_lanes'] for r in results),
            'failed_detail_examples': [dict(r) for r in self.db.execute('SELECT match_id,error FROM candidates WHERE status="failed" LIMIT 10')],
            'config': self.config,
        }
        write_json(self.output / 'report.json', stats)
        if results:
            for name, rows in [('matches.csv', [r['match'] for r in results]),
                               ('players.csv', [p for r in results for p in r['players']])]:
                buffer = io.StringIO(newline='')
                writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
                atomic_bytes(self.output / name, buffer.getvalue().encode('utf-8-sig'))
            lines = ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in results)
            atomic_bytes(self.output / 'matches.jsonl', lines.encode())
        self.set_meta('status', {'status': status, 'reason': reason})
        print(json.dumps({'status': status, 'reason': reason, 'details': len(results),
                          'attempted': stats['unique_details_attempted'], 'requests': stats['http_requests'],
                          'parsed': counts['has_parsed'], 'complete_positions': counts['roles_complete'],
                          'complete_7_minute': counts['early_420_complete'],
                          'remaining_day': stats['last_response_headers'].get('x-rate-limit-remaining-day')}, ensure_ascii=False), flush=True)
        self.last_export = time.monotonic()
        return stats

    def run(self):
        status, reason = 'running', None
        self.export(status)
        try:
            while True:
                completed = self.db.execute('SELECT COUNT(*) FROM candidates WHERE status IN ("ok","failed")').fetchone()[0]
                if completed >= self.args.detail_limit:
                    status = 'detail_trial_complete'
                    break
                candidate = self.db.execute('SELECT * FROM candidates WHERE status="pending" ORDER BY attempted DESC,match_id DESC LIMIT 1').fetchone()
                if candidate:
                    self.collect_detail(candidate)
                else:
                    self.discover()
                if time.monotonic() - self.last_export >= 30:
                    self.export('running')
        except PauseCollection as exc:
            status, reason = 'paused', str(exc)
        except KeyboardInterrupt:
            status, reason = 'paused', 'interrupted'
        except Exception as exc:
            status, reason = 'failed', f'{type(exc).__name__}: {exc}'
        finally:
            stats = self.export(status, reason)
        return 0 if status == 'detail_trial_complete' else 75 if status == 'paused' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='data/opendota/pilot-1000')
    parser.add_argument('--source', choices=['public', 'parsed'], default='public',
                        help='parsed fetches all listed IDs; evaluate target mode after fetching details')
    parser.add_argument('--detail-limit', type=int, default=1000)
    parser.add_argument('--rpm', type=float, default=50)
    parser.add_argument('--daily-budget', type=int, default=2700)
    parser.add_argument('--daily-reserve', type=int, default=100)
    parser.add_argument('--min-age-hours', type=float, default=2)
    parser.add_argument('--max-age-days', type=float, default=7)
    parser.add_argument('--max-list-pages', type=int, default=1000)
    args = parser.parse_args(argv)
    if not 0 < args.rpm <= 50 or args.detail_limit <= 0 or args.daily_budget <= 0 or args.daily_reserve < 0:
        parser.error('invalid limits: RPM must be 0–50 and budgets positive')
    if args.min_age_hours < 0 or args.max_age_days <= args.min_age_hours / 24:
        parser.error('invalid age window')
    return Collector(args).run()


if __name__ == '__main__':
    sys.exit(main())
