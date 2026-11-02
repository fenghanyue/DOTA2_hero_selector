#!/usr/bin/env python3
"""Audit the parsed-ID pilot and compare its ranked-data yield with the first pilot."""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import sqlite3
from zoneinfo import ZoneInfo


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def raw_checks(raw, reference_patch):
    players = raw.get('players', [])
    slots = [p.get('player_slot') for p in players]
    heroes = [p.get('hero_id') for p in players]
    ten_players = (len(players) == 10 and all(type(s) is int for s in slots)
                   and set(slots) == set(range(5)) | set(range(128, 133)))
    basics = (ten_players and type(raw.get('radiant_win')) is bool
              and all(type(h) is int and h > 0 for h in heroes) and len(set(heroes)) == 10
              and all(type(raw.get(k)) is int for k in ('patch', 'duration', 'start_time'))
              and raw['duration'] >= 0)
    roles = ten_players and all(
        all(type(p.get('position_est')) is int for p in players if (p['player_slot'] < 128) == side)
        and {p.get('position_est') for p in players if (p['player_slot'] < 128) == side} == set(range(1, 6))
        for side in (True, False))
    lanes = ten_players and all(
        type(p.get('lane')) is int and p['lane'] in range(1, 6)
        and type(p.get('lane_role')) is int and p['lane_role'] in range(1, 5)
        and type(p.get('is_roaming')) is bool for p in players)
    early = ten_players and all(
        isinstance(p.get('times'), list) and p['times'].count(420) == 1
        and all(isinstance(p.get(k), list) and len(p[k]) == len(p['times'])
                and number(p[k][p['times'].index(420)]) for k in ('networth_t', 'xp_t', 'lh_t'))
        for p in players)
    target = raw.get('game_mode') == 22 and raw.get('lobby_type') == 7
    fields = basics and target and roles and lanes and early
    no_abandon = ten_players and all(type(p.get('leaver_status')) is int
                                     and p['leaver_status'] in (0, 1) for p in players)
    current_patch = raw.get('patch') == reference_patch
    duration_ok = type(raw.get('duration')) is int and raw['duration'] >= 720
    return dict(basics=bool(basics), target=target, roles=bool(roles), lanes=bool(lanes),
                early=bool(early), fields=bool(fields), no_abandon=bool(no_abandon),
                current_patch=current_patch, duration_ok=duration_ok,
                screened=bool(fields and no_abandon and current_patch and duration_ok))


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data/opendota/parsed-pilot-100')
    parser.add_argument('--baseline-dir', default='data/opendota/pilot-1000')
    parser.add_argument('--output', default='docs/opendota-parsed-pilot-report.md')
    args = parser.parse_args()
    folder, baseline = pathlib.Path(args.data_dir).resolve(), pathlib.Path(args.baseline_dir).resolve()
    output = pathlib.Path(args.output).resolve()
    report = json.loads((folder / 'report.json').read_text())
    old_report = json.loads((baseline / 'report.json').read_text())
    assert report['status'] == 'detail_trial_complete', 'Wait for the trial to complete.'
    assert report['config']['source'] == 'parsed'
    assert len(old_report['patch_counts']) == 1, 'Choose a reference patch explicitly if baseline spans patches.'
    patch = int(next(iter(old_report['patch_counts'])))
    db = sqlite3.connect(f'file:{folder / "state.sqlite3"}?mode=ro', uri=True)
    records = [json.loads(r[0]) for r in db.execute('SELECT normalized FROM candidates WHERE status="ok" ORDER BY match_id DESC')]
    assert len(records) == report['successful_detail_responses']
    assert [json.loads(s) for s in (folder / 'matches.jsonl').read_text().splitlines() if s] == records
    matches, players = read_csv(folder / 'matches.csv'), read_csv(folder / 'players.csv')
    assert len(matches) == len(records)
    assert len(players) == sum(len(r['players']) for r in records)
    assert len({(p['match_id'], p['player_slot']) for p in players}) == len(players)
    counts, mode_counts, patches = collections.Counter(), collections.Counter(), collections.Counter()
    by_mode = collections.defaultdict(collections.Counter)
    screened_ids, field_ids, dates, incomplete, raw_by_id = [], [], [], [], {}
    metric_checks = 0
    for record in records:
        mid, m = record['match']['match_id'], record['match']
        body = gzip.decompress((folder / f'raw/matches/{mid}.json.gz').read_bytes())
        provenance = json.loads((folder / f'raw/matches/{mid}.source.json').read_text())
        assert hashlib.sha256(body).hexdigest() == provenance['sha256'], mid
        raw = json.loads(body)
        raw_by_id[mid] = raw
        assert raw['match_id'] == mid
        for key in ('radiant_win', 'game_mode', 'lobby_type', 'patch', 'duration', 'start_time'):
            assert type(raw.get(key)) is type(m[key]) and raw.get(key) == m[key], (mid, key)
        assert {(p['player_slot'], p['hero_id']) for p in raw['players']} == {(p['player_slot'], p['hero_id']) for p in record['players']}
        normalized = {p['player_slot']: p for p in record['players']}
        for p in raw['players']:
            assert normalized[p['player_slot']]['position_est'] == p.get('position_est'), mid
            for seconds in (360, 420):
                for key, label in [('networth_t', 'networth'), ('gold_t', 'earned_gold'), ('xp_t', 'xp'), ('lh_t', 'last_hits'), ('dn_t', 'denies')]:
                    times, values = p.get('times'), p.get(key)
                    expected = None
                    if isinstance(times, list) and isinstance(values, list) and len(times) == len(values) and times.count(seconds) == 1:
                        value = values[times.index(seconds)]
                        expected = value if number(value) else None
                    assert normalized[p['player_slot']][f'{label}_{seconds}'] == expected, (mid, key, seconds)
                    metric_checks += 1
        check = raw_checks(raw, patch)
        assert m['basic_ok'] == (check['basics'] and check['target'])
        assert m['roles_complete'] == (m['basic_ok'] and check['roles'])
        assert m['laning_metrics_ready'] == check['fields']
        counts.update({k: int(v) for k, v in check.items()})
        counts['parsed'] += (raw.get('od_data') or {}).get('has_parsed') is True
        mode = f'{raw.get("game_mode")}/{raw.get("lobby_type")}'
        mode_counts[mode] += 1
        patches[str(raw.get('patch'))] += 1
        by_mode[mode].update({'details': 1, 'roles': int(check['roles']), 'early': int(check['early'])})
        if check['fields']:
            field_ids.append(mid)
        if check['screened']:
            screened_ids.append(mid)
        if not check['roles']:
            incomplete.append({'match_id': mid, 'duration': raw.get('duration'), 'mode_lobby': mode})
        if type(raw.get('start_time')) is int:
            dates.append(raw['start_time'])
    old_records = [json.loads(s) for s in (baseline / 'matches.jsonl').read_text().splitlines() if s]
    old_ids = {r['match']['match_id'] for r in old_records}
    old_fields = sum(r['match']['laning_metrics_ready'] for r in old_records)
    old_screened = 0
    for r in old_records:
        if r['match']['laning_metrics_ready']:
            raw = json.loads(gzip.decompress((baseline / f'raw/matches/{r["match"]["match_id"]}.json.gz').read_bytes()))
            old_screened += raw_checks(raw, patch)['screened']
    integrity = dict(checked_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                     raw_responses_sha256_checked=len(records), matches_csv_rows=len(matches),
                     players_csv_rows=len(players), early_metric_values_compared_to_raw=metric_checks,
                     raw_roles_and_quality_flags_independently_checked=True,
                     jsonl_matches_normalized_database=True)
    analysis = dict(reference_patch=patch, counts=dict(counts), mode_lobby_counts=dict(mode_counts),
                    by_mode=dict(by_mode), patch_counts=dict(patches), field_complete_target_ids=field_ids,
                    screened_target_ids=screened_ids, incomplete_roles=incomplete,
                    overlaps_previous_pilot=len(set(raw_by_id) & old_ids),
                    new_screened_ids=[mid for mid in screened_ids if mid not in old_ids],
                    baseline_field_complete=old_fields, baseline_screened=old_screened,
                    http_requests=report['http_requests'], baseline_http_requests=old_report['http_requests'])
    for filename, value in [('integrity.json', integrity), ('analysis.json', analysis)]:
        (folder / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    screened = [r for r in records if r['match']['match_id'] in set(screened_ids)]
    (folder / 'screened-matches.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in screened))
    def local(timestamp):
        return dt.datetime.fromtimestamp(timestamp, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    def link(name):
        return os.path.relpath(folder / name, output.parent)
    def cost(requests, count):
        return f'{requests / count:.2f}' if count else '本批未取得合格样本'
    n = len(records)
    text = ['# OpenDota 已解析比赛入口试采结果', '',
            f'实际运行完成：请求{report["unique_details_attempted"]}个不同ID，取得{n}份详情，失败{report["failed_details"]}场。', '',
            '## 1. 采样方式', '',
            '- 首次请求 `GET /parsedMatches`，冻结该页ID，按ID倒序获取前100个；不是100场天梯，也不为凑够天梯数量继续翻页。',
            '- 这个列表只有ID，必须先读详情才能区分天梯、普通匹配、加速和比赛。本次不按结束两小时或七天窗口预筛。',
            '- 目标仍为 `game_mode=22`、`lobby_type=7`。所需字段为十人英雄、胜负、各队完整1～5号位推算、分路，以及7:00净资产、经验和正补。',
            f'- 另做质量预筛：版本内部编号与上一批相同（{patch}）、时长至少12分钟、十人 `leaver_status` 均为0或1；缺失离开状态也不纳入此预筛。',
            '- 这些条件只排除明显不合适的记录，不证明职责估计准确，也不代表已得到线优线劣标签。', '',
            '## 2. 实测结果', '', '| 检查项 | 场数 |', '|---|---:|',
            f'| 成功详情 | {n} |', f'| 标记已有录像解析 | {counts["parsed"]} |',
            f'| 位置字段齐全，不限模式 | {counts["roles"]} |',
            f'| 7:00主要曲线齐全，不限模式 | {counts["early"]} |',
            f'| 目标天梯模式 | {counts["target"]} |',
            f'| 目标天梯且所需字段齐全 | {counts["fields"]} |',
            f'| 再通过同版本、时长和离开状态预筛 | {counts["screened"]} |', '',
            '## 3. 模式构成', '', '| game_mode / lobby_type | 详情 | 位置齐全 | 7:00曲线齐全 |', '|---|---:|---:|---:|']
    names = {'22/7': '排位全英雄选择', '22/0': '普通全英雄选择', '23/0': '加速模式', '2/1': '队长模式／练习房间'}
    for mode, c in sorted(by_mode.items(), key=lambda x: (-x[1]['details'], x[0])):
        text.append(f'| {mode}（{names.get(mode, "其他模式／房间类型")}） | {c["details"]} | {c["roles"]} | {c["early"]} |')
    text += ['', '房间类型本身不能证明是职业比赛；原始详情保留联赛和队伍信息，后续可另行分类。', '',
             '## 4. 与普通公开比赛入口对照', '',
             '| 检查项 | 上一批 publicMatches | 本批 parsedMatches |', '|---|---:|---:|',
             f'| 获取详情 | {len(old_records)} | {n} |',
             f'| 天梯且所需字段齐全 | {old_fields} | {counts["fields"]} |',
             f'| 同版本、时长和离开状态预筛通过 | {old_screened} | {counts["screened"]} |',
             f'| HTTP请求，含列表和重试 | {old_report["http_requests"]} | {report["http_requests"]} |',
             f'| 每份字段齐全天梯样本的HTTP请求 | {cost(old_report["http_requests"], old_fields)} | {cost(report["http_requests"], counts["fields"])} |', '',
             '这是两批不同时间、不同规模、不同选择条件的渠道试采，用于判断获取效率，不能据此推断全体天梯的解析覆盖率。',
             '已解析列表包含自动解析和主动请求解析的结果，样本可能集中于特定玩家、模式或分段，不是全体天梯的均匀随机样本。',
             f'本批与上一批重合{analysis["overlaps_previous_pilot"]}个比赛ID；通过预筛且不在上一批的有{len(analysis["new_screened_ids"])}场。', '',
             '## 5. 时间、版本与完整性', '',
             f'实际比赛开始时间：{local(min(dates))}至{local(max(dates))}（北京时间）。' if dates else '没有可用开始时间。',
             f'版本内部编号分布：`{json.dumps(dict(patches), ensure_ascii=False)}`。',
             f'位置不完整的记录：`{json.dumps(incomplete, ensure_ascii=False)}`。',
             f'实际HTTP请求{report["http_requests"]}次，其中列表{report["http_list_requests"]}次、详情（含重试）{report["http_detail_requests"]}次；末次响应的当日剩余额度为{report["last_response_headers"].get("x-rate-limit-remaining-day", "未返回")}。',
             f'已核对{n}份原始响应的SHA-256、ID、英雄槽位、模式和胜负，独立核对位置及字段完整性标记，并核对{metric_checks}个6/7分钟指标值或缺失值。',
             f'JSONL与数据库一致，CSV有{len(matches)}行比赛、{len(players)}行玩家。未做位置标签人工验收或模型训练。', '',
             '## 6. 文件', '',
             f'- [分析明细 analysis.json]({link("analysis.json")})：各项统计与合格比赛ID。',
             f'- [全部比赛 matches.csv]({link("matches.csv")})、[玩家 players.csv]({link("players.csv")})、[关联数据 matches.jsonl]({link("matches.jsonl")})。',
             f'- [预筛通过的天梯记录 screened-matches.jsonl]({link("screened-matches.jsonl")})。',
             f'- [采集统计 report.json]({link("report.json")})、[完整性核对 integrity.json]({link("integrity.json")})。',
             '- `raw/matches/`保留逐场完整压缩响应和来源；本批数据独立保存，上一批未覆盖。数据目录由Git忽略，仅在当前工作区。', '',
             '## 7. 复现', '', '```bash',
             'python scripts/collect_opendota.py --source parsed --output data/opendota/parsed-pilot-100 --detail-limit 100 --max-list-pages 1 --daily-budget 180 --rpm 50',
             'python scripts/analyze_opendota_parsed_pilot.py', '```', '',
             '相同目录重跑使用缓存，不额外获取新的一页；新试采必须使用新目录。没有提交新增录像解析任务，没有自动扩大采集。', '',
             '官方实现依据：[已解析ID列表](https://github.com/odota/core/blob/master/svc/api/spec.ts#L1190)、[自动解析条件](https://github.com/odota/core/blob/master/svc/util/insert.ts#L475)、[位置推算](https://github.com/odota/core/blob/master/svc/util/compute.ts#L254)。', '']
    output.write_text('\n'.join(text))
    print(json.dumps({'report': str(output), 'analysis': analysis, 'integrity': integrity}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
