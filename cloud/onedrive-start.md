## 个人 OneDrive 数据备份

保留现有项目读取、Java/Android、环境加载、应用启动与验证职责。本段只增加 OneDrive 接入。

- 环境配置须声明可选个人环境变量 `ONEDRIVE_RCLONE_CONFIG_B64`，由本人的 Personal vault 提供，限定此环境。它是程序需要直接读取的环境变量，不使用 HTTP 网络机密占位符。仅写这段说明不会创建变量请求，需在环境设置中保存该要求。
- 工具由安装脚本固定到 `/workspace/.cloud-environment/onedrive-tools/`；来源提交和 SHA-256 在该目录的 `sources.json`，rclone 固定为 1.76.0。即使项目默认分支尚未包含接入脚本，发布的工具仍可使用。
- 安装和发布阶段只准备程序，不把授权解码写入准备好的文件系统。实际任务第一次需要 OneDrive 时，运行 `python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py bootstrap`，再用 `status` 验证权限和剩余空间；不列出无关个人文件。
- 没有个人变量时保留其他功能，明确报告 OneDrive 尚未连接。程序会把运行时配置放在用户私有目录，权限为 0600；同一保管库种子不会覆盖已续期的配置。
- 只向 `dota_onedrive:DOTA2_hero_selector/opendota/<batch>/` 上传约定的归档、训练文件及报告。使用 `upload` 后须逐文件回读 SHA-256，完成清单最后上传；以实际校验报告判断交付。
- 不回显或提交授权值、不运行 `rclone config show/dump` 把内容输出到聊天或日志，不向聊天索取密码、令牌或授权配置。`vault_update_required=true` 时，用 `export-vault` 写出仓库外的受限权限文件，由用户更新 Personal vault；本任务无法自动回写保管库。
- 保存并重新发布环境后，由用户新开任务，确认个人变量注入、恢复连接和已有备份回读；验收前标记待验证。发布快照中只保留程序，不包含解码后的个人授权文件。
