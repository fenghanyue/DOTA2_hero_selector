# OpenDota 采集脚本使用说明

初版使用天梯的十个英雄、双方 1～5 号位和胜负，配合版本、时间及整场近似分段。当前进展与字段示例见 [进展文档](opendota-progress.md)。

## 1. 启动

Python 3.10 及以上，无第三方包依赖。在仓库根目录运行：

```bash
python scripts/collect_opendota.py --source parsed --output data/opendota/parsed-demo --detail-limit 100 --max-list-pages 1 --daily-budget 180 --rpm 50
```

工作区已有环境加载脚本时，先执行 `source /workspace/.cloud-environment/env.sh`；普通本地环境使用已安装的 Python 即可。

运行时保留 `--source parsed`，从 `/parsedMatches` 获取已解析比赛 ID，再逐场读取 `/matches/{match_id}`。列表包含多种模式，目标天梯由详情里的 `game_mode=22`、`lobby_type=7` 判断；已解析也可能缺少位置。

`--detail-limit 100` 是处理 100 个不同 ID，成功与最终失败均计数，不代表取得 100 场有效天梯。这是带请求上限的小规模验证命令，没有在后台继续补足有效样本。

## 2. 参数、额度和续采

| 参数 | 说明 |
|---|---|
| `--source parsed` | 使用已解析 ID 入口，筛选目标模式之前先读取详情 |
| `--output` | 本批缓存、数据库和整理结果的目录 |
| `--detail-limit` | 此输出目录累计处理的不同比赛 ID 数量上限 |
| `--max-list-pages` | 此输出目录累计读取的列表页数上限，每页最多 100 个 ID |
| `--rpm` | 每分钟请求数上限，脚本允许最高 50；列表、详情和重试都计数 |
| `--daily-budget` | 此输出目录数据库记录的单日请求预算，默认 2700；上面的验证命令设为 180 |
| `--daily-reserve` | 根据服务端响应头预留的日额度，默认 100 |

免费接口有调用额度。脚本同时检查本地预算和服务端返回的日剩余额度；多目录、多进程以及其他客户端的消耗不能仅靠某一个目录的请求数推算。

再次运行同一命令会沿用缓存、游标和已有状态，成功详情不会重复下载；同一输出目录只允许一个采集进程。继续增加本批数量时，需要相应提高 `--detail-limit` 和 `--max-list-pages`；另一批独立采样应使用新目录。不同来源不能混用同一个输出目录。

达到请求预算、页数或服务端限制时保存进度并停止。没有创建次日自动续跑任务。当前命令只请求已有数据，没有提交新增录像解析任务。

## 3. 保存的文件

上面的命令输出到 `data/opendota/parsed-demo/`；`data/` 已由 Git 忽略。

| 文件 | 内容 |
|---|---|
| `config.json` | 来源、目标模式、采样口径和初始配置 |
| `state.sqlite3` | 分页游标、候选 ID、处理状态、请求记录和整理结果 |
| `raw/http/*.json.gz` | 各次实际 HTTP 响应的压缩原始字节 |
| `raw/matches/{match_id}.json.gz` | 成功取得的单场完整原始响应缓存 |
| `raw/matches/{match_id}.source.json` | 请求地址、响应头、原始响应 SHA-256 和请求序号 |
| `matches.csv` | 每场背景、胜负、近似分段及字段完整性标记 |
| `players.csv` | 每名玩家的英雄、阵营、推算位置及其他已整理字段 |
| `matches.jsonl` | 每行一个关联的 `match` / `players` 对象 |
| `report.json` | 请求数、成功数、完整率、版本分布和停止原因 |

CSV 空单元格表示未取得对应值；JSONL 用 `null` 保留缺失。数值 0 和布尔值 `false` 均按实际值保留。原始响应不会被补造字段。

当前采集输出保留的列比初版最小结构更多；[样例](../samples/opendota/README.md)展示了进一步压缩后的格式，批量固定格式导出仍待补充。

## 4. 初版筛选和近似分段

`position_training_ready` 表示以下条件同时满足：

- `game_mode=22`、`lobby_type=7`，且胜负为布尔值。
- 具有十个不同的有效英雄、双方各五个合法玩家槽位，版本、时间和时长字段有效。
- 每方位置估计恰好组成 1、2、3、4、5 各一个。

`position_est` 来自 OpenDota 根据约 10～12 分钟发育和分路做出的估计。字段齐全不证明真实职责准确，较短比赛可能缺少它。初版阵容样本不要求分路、7 分钟曲线或事件日志完整。

现有数据另做的基础质量预筛为：版本编号 60、时长至少 720 秒、十人 `leaver_status` 均为 0 或 1。它目前用于报告合格样本数量，没有在采集时删除原始响应或替代完整性标记。

整场近似分段新增两个字段：

| 字段 | 含义 |
|---|---|
| `rank_band_est` | 中文大段位；全缺失时为 `null` |
| `rank_known_players` | 参与估算的已知玩家人数，0～10 |

规则是取有效玩家 `rank_tier`，忽略星级，对大段位取较低中位数；少于 5 人时按“依据较少”解读。无段位数据不会使阵容样本失去资格。这是依据已知玩家资料得到的整场近似分层，不是历史精确天梯分。

采集器另外保留的曲线和事件字段属于可选整理结果，不参与上述初版位置样本条件。

## 5. 验证

```bash
python -m unittest discover -s tests -v
```

当前 17 项测试覆盖段位中位数及缺失、位置与英雄重复、模式、胜负与零值、采样时间、事件边界、分页去重、缓存续采、预算和跨日额度状态。模拟测试不能替代真实字段覆盖率或位置准确率验收。

`scripts/analyze_opendota_parsed_pilot.py` 用于复核已有试采目录的原始哈希、字段、CSV 和数据库；完整运行需要对应的本地试采数据目录，仓库内的三场样例不替代那些运行数据。

## 6. 原始依据

- [OpenDota API 文档](https://docs.opendota.com/)。
- [已解析 ID 列表](https://github.com/odota/core/blob/master/svc/api/spec.ts#L1190)。
- [位置估计](https://github.com/odota/core/blob/master/svc/util/compute.ts#L254)。
- [接口限额配置](https://github.com/odota/core/blob/master/config.ts)。
