# DOTA2 选人推荐器

目标是提供 Android APK 和静态网页：输入当前双方选人、ban 人和自己的位置，推荐最多 5 个英雄。

目前处于 OpenDota 数据采集与初版方案验证阶段，推荐模型、APK 和网页尚未实现。

- [OpenDota 数据进展与初版方案](docs/opendota-progress.md)
- [采集脚本使用说明](docs/opendota-collection.md)
- [真实样例与字段说明](samples/opendota/README.md)
- [个人 OneDrive：云端备份与恢复](docs/onedrive-backup.md)

Python 3.10 及以上，无第三方包依赖。在仓库根目录运行：

```bash
python scripts/collect_opendota.py --source ranked --output data/opendota/ranked-demo --detail-limit 100 --max-list-pages 1 --request-limit 180 --daily-budget 180 --rpm 50
python -m unittest discover -s tests -v
```

`--source ranked` 通过 Explorer 直接取得全英雄选择天梯 ID，再读取 Valve 元数据中的官方职责，无需先解析录像。`--detail-limit 100` 表示处理 100 个比赛 ID，成功和失败都计入，合格样本需要另行检查版本、职责、英雄、胜负、时长和离开状态。

完整运行数据保存在云工作区的 `data/`，默认由 Git 忽略；训练数据和报告需显式提交到 `samples/`，完整原始数据另做 GitHub 归档。目录存在于云环境不等于已经保存到你的电脑或 GitHub。历史计数与当前可恢复数据请以进展文档中最新日期的记录为准。

OneDrive 的 `upload`、`verify`、`restore` 已完成当前任务真实存取验收和 50 项离线测试。[开发任务实测](samples/opendota/ranked-official-20261112-1000/onedrive-storage-e2e-20261113.json)。2026-11-13 用户重新发布环境后，在独立新任务实测工具自动恢复、授权恢复、原备份校验恢复、新批次上传校验和新批次恢复，五项全部通过。[新任务验收记录](samples/opendota/ranked-official-20261112-1000/onedrive-published-acceptance-20261113.json)。当前约定的临时方案已交付：显式使用 Graph beta 读取，单文件上传上限 200 MiB；原 401 根因仍未确定。操作见 [OneDrive 说明](docs/onedrive-backup.md)。
