## 个人 OneDrive 数据备份

保留现有项目读取、Java/Android、环境加载、应用启动与验证职责。本段只增加 OneDrive 接入。

- 环境配置须请求个人环境变量 `ONEDRIVE_RCLONE_CONFIG_B64`，沿用本人的 Personal vault 绑定、限定环境范围及现有 optional 设置。它是程序直接读取的变量，不使用 HTTP 网络机密占位符。仅写本段不会创建或更新变量绑定。
- 工具由安装脚本固定到 `/workspace/.cloud-environment/onedrive-tools/`；来源提交和 SHA-256 在该目录的 `sources.json`，rclone 固定为 1.76.0。即使项目默认分支尚未包含接入脚本，发布的工具仍可使用。
- 安装和发布阶段只准备程序，不把授权解码写入准备好的文件系统。实际任务第一次需要 OneDrive 时，运行 `python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py bootstrap`，再用 `status` 验证权限和剩余空间；不列出无关个人文件。
- 没有个人变量时保留其他功能，明确报告 OneDrive 尚未连接。程序会把运行时配置放在用户私有目录，权限为 0600；同一保管库种子不会覆盖已续期的配置。
- 只操作 `dota_onedrive:DOTA2_hero_selector/opendota/<batch>/` 内的项目备份。采集并不会自动触发上传，也没有无人值守计划。按本次任务授权执行采集、打包、上传和恢复。
- 用户已接受当前实验项目的临时读取方案：`--read-method graph-contentstream` 显式选用 Microsoft Graph beta，不能声称它是微软支持的生产接口。标准模式仍为默认，不静默切换读取模式。当前临时上传方案单文件上限为 200 MiB；rclone 对这些文件使用正式 Graph 单次上传，避免已出现 401 的预认证上传会话。超限时先拆小采集批次，不删除原数据或偷偷改上传方案。
- 现成命令（均使用 `/workspace/.cloud-environment/onedrive-tools/onedrive_backup.py`）：
  - `upload --archive <原始.tar.zst> --training <训练目录> --batch <批次> --report <本地报告.json> --read-method graph-contentstream`
  - `verify --batch <批次> --report <本地报告.json> --read-method graph-contentstream`
  - `restore --batch <批次> --output <尚不存在的恢复目录> --read-method graph-contentstream`
- 上传须逐文件回读 SHA-256，完成清单最后上传且不可覆盖；同批次相同内容再次执行时只校验，不重复写入。不同内容使用新批次。恢复先在临时目录下载全部文件，大小与哈希全部匹配后才生成目标目录；已有目录不覆盖。恢复结果包含 `raw/`、`training/`、`manifest.json` 和 `restore-report.json`，原始压缩包不自动解压执行。
- 不回显或提交授权值、不运行 `rclone config show/dump` 把内容输出到聊天或日志，不向聊天索取密码、令牌或授权配置。`vault_update_required=true` 时，用 `export-vault` 写出仓库外的受限权限文件，由用户更新 Personal vault；本任务无法自动回写保管库。
- 保存并重新发布环境后，由用户新开任务，确认个人变量注入、恢复连接和已有备份回读；验收前标记待验证。发布快照中只保留程序，不包含解码后的个人授权文件。
