# 2026-11-12 官方职责批次

处理 1000 个新天梯 ID，成功取得 969 场详情；881 场职责齐全，最终导出 **830 场**。合格要求版本 60、时长至少 720 秒、十人英雄/胜负/官方职责齐全、离开状态均为 0 或 1，并通过来源与原始哈希核对。无需录像已解析；其中 825 场合格比赛未解析。

比赛开始时间为 2026-11-12 02:35:42～02:48:32 UTC，覆盖八个大段位。它是一个短时段、单一版本的采集批次，模型尚未训练。

## 文件

| 文件 | 内容 |
|---|---|
| `matches.jsonl` | 830 行最小训练数据，双方英雄依次为 1～5 号位 |
| `provenance.json` | 每场原始 SHA-256、来源 URL 和缓存路径，以及采样配置 |
| `report.json` | 导出筛选、拒绝原因、分段与数据集哈希 |
| `collection-report.json` | 累计请求及质量统计，运行耗时字段针对最后一次运行 |
| `collection-summary.json` | 两次运行合计耗时、限速、互斥排除分类、采样时间和胜负分布 |
| `audit.json` | 独立原始/字段/数据库/CSV/JSONL 审计结果 |
| `test-report.json` | 29 项离线自动化测试、代码提交与日志哈希 |
| `backup.json` | 已验证的 GitHub 备份提交、归档与分段 SHA-256 |
| `restore-verification.json` | 实际解压 3033 个文件的哈希和恢复副本审计结果 |
| `raw/match-9086431243.json.gz` / `.source.json` | 一场完整真实响应与原始来源；元数据含两个主队及八个空占位队，供回归验证 |

原始缓存路径在完整归档内；本目录只另外保留一场原始响应。拒绝原因可能重叠：59 场职责全 0、29 场元数据缺失、55 场离开状态不合格，其中 4 场也缺少职责，因此详情去重排除数为 139；另有 31 个 ID 的详情最终失败。

## 真实记录

下面的英雄数组来自官方职责标记；原始 `position_est` 缺失也可通过。

```json
{
  "match_id": 9086431243,
  "start_time": 1794451712,
  "game_mode": 22,
  "lobby_type": 7,
  "patch": 60,
  "rank_band_est": "传奇",
  "rank_known_players": 9,
  "radiant": [
    73,
    47,
    29,
    14,
    9
  ],
  "dire": [
    145,
    76,
    104,
    39,
    100
  ],
  "radiant_win": false,
  "position_source": "valve_metadata_lane_selection_flags"
}
```

`position_source` 是 `valve_metadata_lane_selection_flags`。旧目录的 3 场推断职责样例未并入此文件。最小数据 SHA-256 为 `03c4ef80157f6a008a1b3d7373fe3f855ec20de840d9f2435651cf7e84006988`。

## 完整备份

[GitHub 备份目录及恢复说明](https://github.com/fenghanyue/DOTA2_hero_selector/tree/1b4b304ad5a13ea124257f363fcf36288eca6479/backups/20261112)。两段归档总计 31,221,754 字节；完整目录包含所有成功及失败响应、SQLite、原始详情、整理结果和运行日志，恢复验证通过。归档 SHA-256 为 `bd472af4f8ca69821a6c29fc3268fc733beec8c169cd805a78b6ea0a97441bb5`。

采集与导出命令、筛选定义、官方协议来源见[使用说明](../../../docs/opendota-collection.md)，整个项目状态见[进展](../../../docs/opendota-progress.md)。
