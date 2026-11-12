# 2026-11-12 OpenDota 完整原始备份

本备份保留 1000 个天梯 ID 的采集状态：969 场成功详情、31 个最终失败 ID、1084 次请求记录；其中导出 830 场官方职责样本。包含全部已保存 HTTP 响应、完整详情及来源、SQLite、CSV/JSONL、两次运行报告、日志、审计和测试记录。

归档为 Zstandard 压缩 tar，分成两段，每段不超过 24 MiB。文件 SHA-256 和完整归档 SHA-256 见 manifest.json。训练集与代码在 data/opendota-official-20261112 分支；本分支用于备份，不需要合并到 main。

在仓库根目录恢复到独立目录（需要 git、tar、zstd）：

```bash
git fetch origin data/opendota-backup-20261112
git archive FETCH_HEAD backups/20261112 | tar -x
sha256sum -c backups/20261112/SHA256SUMS
cat backups/20261112/ranked-official-20261112-1000.tar.zst.part-* > /tmp/ranked-official-20261112-1000.tar.zst
mkdir -p /tmp/opendota-recovered
tar --zstd -xf /tmp/ranked-official-20261112-1000.tar.zst -C /tmp/opendota-recovered
cd /tmp/opendota-recovered/data/opendota/ranked-official-20261112-1000
sha256sum -c FILES.sha256
```

解压后仍是采集器原来的目录结构，可用保存的代码重新导出及审计。云工作区中的目录不自动同步到个人电脑，本分支是已经显式推送的独立副本。
