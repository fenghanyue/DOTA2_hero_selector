# OpenDota 采集与导出

初版使用天梯十个英雄、双方 1～5 号位和胜负，配合版本、时间及整场近似分段。Python 3.10 及以上，无第三方依赖。当前优先使用官方职责，不要求录像已解析。

## 1. 采集

在仓库根目录执行；工作区有环境加载脚本时，先执行 `source /workspace/.cloud-environment/env.sh`。

```bash
python scripts/collect_opendota.py --source ranked \
  --output data/opendota/ranked-official-20261112-1000 \
  --detail-limit 1000 --max-list-pages 10 --request-limit 1500 \
  --daily-budget 1500 --daily-reserve 100 --rpm 50 --min-duration 720
```

链路为 `GET /explorer?sql=...` 查询 `public_matches`，预选 `lobby_type=7 AND game_mode=22 AND duration>=720`，按 ID 递减分页，每页至多 100 场；随后请求 `GET /matches/{id}?meta=1`。没有提交新增录像解析任务，也不下载完整录像。

`--detail-limit` 是该目录累计处理的不同 ID 上限，成功和最终失败都计数；它不是合格场数。一次请求循环最多尝试 3 次；限流暂停时该 ID 保持待处理，续跑可以再尝试，所有重试、列表请求都占累计预算。`--request-limit` 限制该目录跨日累计请求；`--daily-budget` 限制该目录 UTC 单日请求。`--daily-reserve` 根据服务端日剩余额度保留 100 次；其他进程的消耗以响应头为准。`--rpm` 最高 50。

达到任一预算、页数或服务端限制会保存进度并停止；不会次日自动运行。再次运行相同命令沿用游标、缓存和状态，已成功详情不重下。一个目录只允许一个采集进程，不同来源或最低时长不能混用同一目录。

保留的 `--source parsed` 从 `/parsedMatches` 读取各种模式的已解析 ID，详情使用 OpenDota 推断职责；`--source public` 使用公共摘要和时间窗口，同样使用推断职责。这两条旧链路与官方职责数据分目录保存。

## 2. 官方职责

读取详情的 `metadata.data.metadata.teams[].players[].lane_selection_flags`；也支持直接返回的元数据文件结构。先核对元数据比赛 ID，天辉 `dota_team=2`、夜魇 `dota_team=3`，再核对十个玩家槽位。元数据可能包含 6～13 队的空占位，允许跳过；其他队存在玩家则拒绝。

| Valve 标记 | 职责 |
|---|---|
| 1（SAFELANE） | 1 号位 |
| 4（MIDLANE） | 2 号位 |
| 2（OFFLANE） | 3 号位 |
| 8（SUPPORT） | 4 号位 |
| 16（HARDSUPPORT） | 5 号位 |

每队必须恰好五种职责各一个。缺失、0、31 等多选或非单一标记不能作为完整职责。缺少元数据不会回退到推断职责；`official_role_error` 保存原因。该字段记录官方职责标记，不能证明玩家整场遵守该职责或实际站在哪条路。

整理结果的 `position` 是本批选择的职责，`position_source` 是来源；`position_est` 单独保留 OpenDota 推断值。官方来源写作 `valve_metadata_lane_selection_flags`，旧推断来源是 `opendota_position_est`。

## 3. 合格训练样本与导出

停止采集后运行：

```bash
python scripts/export_opendota_training.py \
  --source data/opendota/ranked-official-20261112-1000 \
  --output samples/opendota/ranked-official-20261112-1000 --patch 60
python scripts/audit_opendota_official.py \
  --source data/opendota/ranked-official-20261112-1000 \
  --training samples/opendota/ranked-official-20261112-1000 \
  --output samples/opendota/ranked-official-20261112-1000/audit.json
```

版本需依据本批真实详情确认后显式指定；命令中的 60 是本次已取得详情的编号，不代表未来版本固定。合格须同时满足：

- 全英雄选择天梯，十个不同的正整数英雄 ID，双方各五个合法槽位，胜负为布尔值。
- 开始时间、版本和时长字段有效，版本等于指定值，时长至少 720 秒。
- 官方元数据 ID 对应、双方官方职责各含完整的 1～5 号位。
- 十人 `leaver_status` 都是整数 0 或 1；缺失也排除。1 表示曾断连，允许它不等于证明整场无人掉线。
- 原始响应 SHA-256、请求 URL、数据库记录和缓存原始字节全部一致；来源为 `?meta=1`。

导出器仅接受独立的官方天梯批次，拒绝采集仍在运行、输出覆盖源目录、哈希错误或整理结果与原始响应不符。校验错误会保留在报告中并返回失败；零合格时也覆盖旧输出为空文件，避免残留旧数据。

最小 `matches.jsonl` 每行包含 `match_id`、`start_time`、`game_mode`、`lobby_type`、`patch`、`rank_band_est`、`rank_known_players`、`radiant`、`dire`、`radiant_win`、`position_source`。两个英雄数组依次对应 1～5 号位；`false` 胜负正常保留。

近似分段取已知有效玩家 `rank_tier` 的大段位较低中位数，忽略星级；同时保存有效人数，全部未知则为 `null`、0，允许合格。它来自查询时已知资料，不能当成对局时精确天梯分。

合格不要求分路、7 分钟曲线、事件日志或 `has_parsed=true`。独立审计另外核对所有 HTTP 原始哈希、数据库/JSONL/CSV、官方位标记、英雄顺序、分段、筛选、预算和限速；不证明职责标记就是实际打法或模型效果合格。

## 4. 文件与持久保存

| 位置 | 内容 |
|---|---|
| `data/opendota/<batch>/config.json` | 来源、模式、最低时长等初始口径 |
| `state.sqlite3` | 候选、游标、详情状态、请求与耗时 |
| `raw/http/*.json.gz` | 各次 HTTP 的原始响应，包括错误响应 |
| `raw/matches/<id>.json.gz` / `.source.json` | 成功详情原始缓存与 URL、SHA、响应头 |
| `matches.jsonl`、`matches.csv`、`players.csv` | 完整整理结果，含不合格详情 |
| `report.json` | 完成状态、失败、字段覆盖、请求数与耗时 |
| `samples/opendota/<batch>/` | 最小训练集、逐场来源、采集/导出/独立审计报告及真实回归样例 |

`data/` 由 Git 忽略，只有明确提交并推送的内容才在 GitHub。本批完整目录会在采集停止、审计后打包，含 SQLite、全部原始响应、整理结果、运行日志和校验清单；归档存放独立备份分支，位置及 SHA 见[最新进展](opendota-progress.md)。训练数据和报告放在采集代码分支供审阅。云工作区文件并不会自动同步到电脑。

## 5. 验证与依据

```bash
python -m unittest discover -s tests -v
```

测试覆盖原有推断链路、官方标记映射、空占位队、职责缺失时不回退、元数据 ID 与槽位、分页、缓存、预算、导出哈希/离开状态/版本，以及独立审计发现 CSV 损坏。真实采集的覆盖率另看本批报告。

- [OpenDota API 文档](https://docs.opendota.com/)。
- [OpenDota 元数据获取实现](https://github.com/odota/core/blob/master/svc/fetcher/MetaFetcher.ts)，会下载并解码 Valve 小型元数据文件，未要求先解析整场录像。
- [Valve 元数据结构](https://github.com/SteamTracking/GameTracking-Dota2/blob/master/Protobufs/dota_match_metadata.proto)，`CDOTAMatchMetadataFile.match_id`、`Team.Player.lane_selection_flags`。
- [Valve 职责位标记枚举](https://github.com/SteamTracking/GameTracking-Dota2/blob/master/Protobufs/dota_gcmessages_common_match_management.proto)，`ELaneSelectionFlags`。
- [OpenDota 位置估计](https://github.com/odota/core/blob/master/svc/util/compute.ts)与[限额配置](https://github.com/odota/core/blob/master/config.ts)。

## 6. 继续采集到 5 万场

用户于 2026-11-13 要求继续执行之前的样本量目标：先以 **5000 场合格、去重对局**作为模型基线检查点，继续到 **50000 场**。原有可恢复样本为 830 场；OneDrive 存取验收副本不增加数量。此阶段采集和保存数据，不训练模型。

此前的数学估算是比例估计的近似公式 `n = 1.96² × p(1-p) / e²`：取最保守的 `p=0.5`，95% 置信水平下，允许误差 ±2 个百分点需要该统计对象约 2401 场，±1 个百分点约 9604 场。出现率 10% 的组合取得 2401 场相关比赛，需要总量约 24010 场；稀有组合需要更多。因此 50000 是初版验证目标，不能保证模型准确率。该公式假设近似独立，采样偏差不会随数量自动消除。[NIST 比例区间](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)

后续模型评估预留按时间较晚的约 10000 场最终测试，约 35000 场训练、5000 场验证；同一比赛的所有视角放在同一份数据里。通过学习曲线、概率误差和常见英雄／位置／段位的结果判断是否需要继续扩充。本阶段不把数据达标写成精度达标。

续采入口：

```bash
source /workspace/.cloud-environment/env.sh
python3 -u scripts/continue_opendota_collection.py
```

每次只运行一个 UTC 六小时区间（00、06、12、18）的批次，命名如 `ranked-official-20261113T06`。每批最多处理 600 个 ID、使用 675 次 OpenDota 请求（含列表和重试）、每分钟 40 次；四批不超过 2700 次／UTC 日，并根据服务端响应再预留 300 次。免费额度的原始配置为每天 3000 次，不使用付费密钥。

程序先从 OneDrive 读取符合命名规则的完成清单，再核对训练数据、导出报告和独立审计文件的大小与 SHA-256，按比赛 ID 合并计数。原批次清单有固定 SHA-256；相同副本仅计一次，同 ID 内容冲突则停止，不在远端读取失败时把总数重置为零。

已合格的 ID 不再请求详情。停止的采集批次先导出和独立审计，完整原始目录打成小于 200 MiB 的归档；调用已发布的 OneDrive 工具执行上传及逐文件回读，再单独 `verify`，通过后计入总数。路径仍为 `DOTA2_hero_selector/opendota/<batch>/`；每批包含原始归档、训练集、逐场来源及报告。新数据不整批提交到 GitHub。

每个区间先在 OneDrive 写入并回读 `run-claim.json`，避免新任务丢失本地状态后再次消耗同一区间的额度；同一工作区有进程锁。只有同一自动任务串行执行，不并发启动其他采集器。若任务中断且本地文件丢失，该区间保留占用，不算数据成功；后续区间继续补齐。已有完整批次只读取计数，不重新采集。

本地状态位于 `data/opendota-continuation/`，原始数据在 `data/opendota/<batch>/`。`latest-result.json` 仅在本轮整个流程成功后写入；失败时以进程退出码为准，不把旧报告当成本轮成功。`--batch <已有批次>` 可完成本地保留的停止批次；不允许借用旧区间启动额外采集。达到 50000 场时不再请求比赛；发现版本变化时保留当前批次并报告，后续自动任务须暂停讨论版本范围。

采样仍受 OpenDota 公共比赛列表覆盖限制；分散运行时段和日期改善现有 13 分钟的覆盖，但不构成全体天梯的均匀随机抽样。
