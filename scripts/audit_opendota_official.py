#!/usr/bin/env python3
"""Independently check an official-role cohort and its minimal training export."""
import argparse
import collections
import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import pathlib
import sqlite3


def independent_roles(raw):
    """Intentionally independent of the collector's metadata reader."""
    file = raw.get('metadata')
    if not isinstance(file, dict):
        return None
    if 'data' in file or 'error' in file:
        if file.get('error') is not None:
            return None
        file = file.get('data')
    if not isinstance(file, dict) or str(file.get('match_id')) != str(raw['match_id']):
        return None
    body = file.get('metadata')
    teams = body.get('teams') if isinstance(body, dict) else None
    if not isinstance(teams, list):
        return None
    found, seen = {}, set()
    for team in teams:
        if not isinstance(team, dict) or type(team.get('dota_team')) is not int:
            return None
        side = team['dota_team']
        if side not in (2, 3):
            if team.get('players', []) == []:
                continue
            return None
        if side in seen:
            return None
        seen.add(side)
        players = team.get('players')
        if not isinstance(players, list) or len(players) != 5:
            return None
        slots, roles = set(), set()
        allowed = set(range(5)) if side == 2 else set(range(128, 133))
        for player in players:
            if not isinstance(player, dict):
                return None
            slot, flag = player.get('player_slot'), player.get('lane_selection_flags')
            if type(slot) is not int or slot not in allowed or slot in slots:
                return None
            if type(flag) is not int or flag not in (1, 2, 4, 8, 16):
                return None
            role = {1: 1, 2: 3, 4: 2, 8: 4, 16: 5}[flag]
            if role in roles:
                return None
            slots.add(slot)
            roles.add(role)
            found[slot] = role
    return found if seen == {2, 3} else None


def audit(source, training):
    source, training = pathlib.Path(source).resolve(), pathlib.Path(training).resolve()
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)

    with (source / '.collector.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        db = sqlite3.connect(f'{(source / "state.sqlite3").as_uri()}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            requests = db.execute('SELECT * FROM requests ORDER BY id').fetchall()
            candidates = db.execute('SELECT * FROM candidates ORDER BY match_id DESC').fetchall()
        finally:
            db.close()
        config = json.loads((source / 'config.json').read_text())
        stats = json.loads((source / 'report.json').read_text())
        export = json.loads((training / 'report.json').read_text())
        provenance = json.loads((training / 'provenance.json').read_text())
        check(config['source'] == 'ranked' and config['role_source'] == 'official', 'cohort source')
        check(stats['status'] != 'running', 'collection is not stopped')
        for request in requests:
            if request['raw_path']:
                payload = gzip.decompress((source / request['raw_path']).read_bytes())
                check(hashlib.sha256(payload).hexdigest() == request['sha256'], f'HTTP SHA {request["id"]}')
            check(request['status'] is not None or request['error'] is not None, f'unfinished HTTP {request["id"]}')
        starts = [dt.datetime.fromisoformat(r['started_at']) for r in requests]
        min_interval = min(((b - a).total_seconds() for a, b in zip(starts, starts[1:])), default=None)
        check(min_interval is None or min_interval >= 60 / config['requests_per_minute'] - .05, 'request pacing')
        check(stats['request_limit'] is None or len(requests) <= stats['request_limit'], 'total request budget')
        report_counts = collections.Counter(c['status'] for c in candidates)
        check(stats['successful_detail_responses'] == report_counts['ok'], 'successful detail count')
        check(stats['failed_details'] == report_counts['failed'], 'failed detail count')
        check(stats['http_requests'] == len(requests), 'request count')
        stored = [json.loads(c['normalized']) for c in candidates if c['status'] == 'ok']
        jsonl = [json.loads(line) for line in (source / 'matches.jsonl').read_text().splitlines()]
        check(jsonl == stored, 'DB / JSONL mismatch')
        for filename, expected in [('matches.csv', [r['match'] for r in stored]),
                                   ('players.csv', [p for r in stored for p in r['players']])]:
            with (source / filename).open(encoding='utf-8-sig', newline='') as handle:
                actual = list(csv.DictReader(handle))
            converted = [{k: '' if v is None else str(v) for k, v in row.items()} for row in expected]
            check(actual == converted, f'DB / {filename} mismatch')
        expected_rows, expected_hashes = [], {}
        roles_complete, metadata_team_counts, unparsed = 0, collections.Counter(), 0
        for result in stored:
            match = result['match']
            mid = match['match_id']
            payload = gzip.decompress((source / f'raw/matches/{mid}.json.gz').read_bytes())
            raw = json.loads(payload)
            source_info = json.loads((source / f'raw/matches/{mid}.source.json').read_text())
            digest = hashlib.sha256(payload).hexdigest()
            check(source_info['sha256'] == digest, f'cache SHA {mid}')
            check(source_info['url'] == f'https://api.opendota.com/api/matches/{mid}?meta=1', f'cache URL {mid}')
            check((source / source_info['raw_path']).read_bytes() == (source / f'raw/matches/{mid}.json.gz').read_bytes(), f'cache bytes {mid}')
            check(raw['match_id'] == mid, f'raw ID {mid}')
            roles = independent_roles(raw)
            check(bool(roles) == match['official_roles_complete'], f'official role completeness {mid}')
            roles_complete += bool(roles)
            unparsed += (raw.get('od_data') or {}).get('has_parsed') is False
            file = raw.get('metadata') or {}
            file = file.get('data', file) or {}
            metadata_team_counts[str(len((file.get('metadata') or {}).get('teams') or []))] += 1
            for player in result['players']:
                check(player['position'] == (roles.get(player['player_slot']) if roles else None), f'role {mid}/{player["player_slot"]}')
            tiers = sorted(p['rank_tier'] // 10 for p in raw['players'] if type(p.get('rank_tier')) is int
                           and (p['rank_tier'] == 80 or (1 <= p['rank_tier'] // 10 <= 7 and 1 <= p['rank_tier'] % 10 <= 5)))
            band = ('先锋', '卫士', '中军', '统帅', '传奇', '万古流芳', '超凡入圣', '冠绝一世')[tiers[(len(tiers)-1)//2]-1] if tiers else None
            check(match['rank_band_est'] == band and match['rank_known_players'] == len(tiers), f'rank {mid}')
            players = raw['players']
            slots = [p.get('player_slot') for p in players]
            heroes = [p.get('hero_id') for p in players]
            valid = (len(players) == 10 and all(type(s) is int for s in slots)
                     and set(slots) == set(range(5)) | set(range(128, 133))
                     and all(type(h) is int and h > 0 for h in heroes) and len(set(heroes)) == 10
                     and raw.get('game_mode') == 22 and raw.get('lobby_type') == 7
                     and type(raw.get('radiant_win')) is bool and type(raw.get('start_time')) is int
                     and type(raw.get('patch')) is int and raw['patch'] == export['patch']
                     and type(raw.get('duration')) is int and raw['duration'] >= export['min_duration_seconds']
                     and all(type(p.get('leaver_status')) is int and p['leaver_status'] in (0, 1) for p in players)
                     and roles is not None)
            if valid:
                ordered = sorted(players, key=lambda p: roles[p['player_slot']])
                expected_rows.append({k: raw[k] for k in ('match_id', 'start_time', 'game_mode', 'lobby_type', 'patch')}
                                     | {'rank_band_est': band, 'rank_known_players': len(tiers),
                                        'radiant': [p['hero_id'] for p in ordered if p['player_slot'] < 128],
                                        'dire': [p['hero_id'] for p in ordered if p['player_slot'] >= 128],
                                        'radiant_win': raw['radiant_win'],
                                        'position_source': 'valve_metadata_lane_selection_flags'})
                expected_hashes[mid] = digest
        dataset = (training / 'matches.jsonl').read_bytes()
        actual_rows = [json.loads(line) for line in dataset.splitlines()]
        check(actual_rows == expected_rows, 'independent training selection / hero order')
        check(len(actual_rows) == len({r['match_id'] for r in actual_rows}), 'duplicate training IDs')
        check(hashlib.sha256(dataset).hexdigest() == export['dataset_sha256'], 'dataset SHA')
        check({r['match_id']: r['sha256'] for r in provenance['accepted']} == expected_hashes, 'accepted source manifest')
        check(export['accepted_matches'] == len(expected_rows), 'export count')
        check(not export['integrity_errors'], 'export integrity errors')
        check(export['successful_details'] == export['accepted_matches'] + export['rejected_matches'], 'export partition')
        return {'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'passed': not errors, 'errors': errors,
                'http_responses_checked': len(requests), 'successful_details_checked': len(stored),
                'failed_detail_ids': report_counts['failed'], 'official_roles_complete': roles_complete,
                'unparsed_successful_details': unparsed, 'training_matches_checked': len(expected_rows),
                'metadata_team_count_distribution': dict(metadata_team_counts),
                'minimum_request_interval_seconds': min_interval,
                'checks': ['all HTTP and cached raw SHA-256', 'DB / JSONL / both CSVs',
                           'independent Valve flag mapping and hero ordering', 'independent rank median',
                           'independent patch / duration / leaver screening', 'request budget and pacing',
                           'dataset SHA-256, unique IDs and accepted provenance']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--training', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = audit(args.source, args.training)
    pathlib.Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
