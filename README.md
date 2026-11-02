# DOTA2 选人推荐器

目标是提供 Android APK 和静态网页：输入当前双方选人、ban 人和自己的位置，推荐最多 5 个英雄。

目前处于 OpenDota 数据采集与初版方案验证阶段，推荐模型、APK 和网页尚未实现。

- [OpenDota 数据进展与初版方案](docs/opendota-progress.md)
- [采集脚本使用说明](docs/opendota-collection.md)
- [真实样例与字段说明](samples/opendota/README.md)

Python 3.10 及以上，无第三方包依赖。在仓库根目录运行：

```bash
python scripts/collect_opendota.py --source parsed --output data/opendota/parsed-demo --detail-limit 100 --max-list-pages 1 --daily-budget 180 --rpm 50
python -m unittest discover -s tests -v
```

`--detail-limit 100` 表示处理 100 个比赛 ID，列表中包含多种模式，需要取得详情后再筛选天梯及位置完整的比赛。采集产生的完整数据和运行状态保存在 `data/`，由 Git 忽略；仓库内的少量样例保存在 `samples/`。
