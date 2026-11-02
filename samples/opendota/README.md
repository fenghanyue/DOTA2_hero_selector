# OpenDota 真实样例

样例来源为已采集的 3 场真实天梯，均为 `game_mode=22`、`lobby_type=7`、`patch=60`。样例用于查看结构及离线核对，不是训练集的规模或覆盖率证明。

| 比赛 ID | 近似分段 | 已知段位人数 | 胜方 |
|---|---|---:|---|
| 9072646333 | 冠绝一世 | 10 | 夜魇 |
| 9072646211 | 超凡入圣 | 4 | 夜魇 |
| 9072635162 | 冠绝一世 | 9 | 天辉 |

## 文件

- `matches.jsonl`：每行一场，按初版最小结构整理；`radiant` 和 `dire` 的数组顺序均为 1～5 号位。
- `source-fields.json`：同三场 OpenDota 响应中与初版有关的字段摘录，保留英雄、玩家槽位、位置估计、玩家段位和离开状态等；这是摘录，不是完整响应。
- `provenance.json`：原始接口地址、实际取得时间、HTTP 状态及原始响应 SHA-256。只有第一场附带完整响应，其余两场可查看字段摘录。
- `raw/match-9072646333.json.gz`：第一场的完整 API 响应压缩文件，直接复制原始缓存，没有改写里面的字段；它不是 `.dem` 比赛录像。

## 查看

在仓库根目录执行，无需联网：

```bash
python -m json.tool --json-lines samples/opendota/matches.jsonl
python -m json.tool samples/opendota/source-fields.json
python -c 'import gzip; print(gzip.decompress(open("samples/opendota/raw/match-9072646333.json.gz", "rb").read()).decode())'
```

近似分段按已知玩家大段位取较低中位数，忽略星级；已知人数少于 5 人时按“依据较少”解读。位置使用 OpenDota 的推算字段，尚未人工核对真实职责。再次请求原始接口时，玩家资料和衍生字段可能发生更新；来源哈希对应本次实际保存的响应。
