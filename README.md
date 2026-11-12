# DOTA2 选人推荐器

目标是提供 Android APK 和静态网页：输入当前双方选人、ban 人和自己的位置，推荐最多 5 个英雄。

目前处于 OpenDota 数据采集与初版方案验证阶段，推荐模型、APK 和网页尚未实现。

- [OpenDota 数据进展与初版方案](docs/opendota-progress.md)
- [采集脚本使用说明](docs/opendota-collection.md)
- [真实样例与字段说明](samples/opendota/README.md)
- [个人 OneDrive：Mac 授权与云环境复用](docs/onedrive-backup.md)

Python 3.10 及以上，无第三方包依赖。在仓库根目录运行：

```bash
python scripts/collect_opendota.py --source ranked --output data/opendota/ranked-demo --detail-limit 100 --max-list-pages 1 --request-limit 180 --daily-budget 180 --rpm 50
python -m unittest discover -s tests -v
```

`--source ranked` 通过 Explorer 直接取得全英雄选择天梯 ID，再读取 Valve 元数据中的官方职责，无需先解析录像。`--detail-limit 100` 表示处理 100 个比赛 ID，成功和失败都计入，合格样本需要另行检查版本、职责、英雄、胜负、时长和离开状态。

完整运行数据保存在云工作区的 `data/`，默认由 Git 忽略；训练数据和报告需显式提交到 `samples/`，完整原始数据另做 GitHub 归档。目录存在于云环境不等于已经保存到你的电脑或 GitHub。历史计数与当前可恢复数据请以进展文档中最新日期的记录为准。

2026-11-12 批次已另存个人 OneDrive，11 个文件全部通过上传后回读 SHA-256 校验；[传输报告](samples/opendota/ranked-official-20261112-1000/onedrive-verification.json)已保留。用户已 Promote 变量设置，实际新任务自动恢复连接仍待验收。
