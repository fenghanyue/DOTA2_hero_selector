# 个人 OneDrive：Mac 授权与云环境复用

2026-11-12：当前云任务已完成个人 OneDrive 的真实连接和首批备份验证。原始归档与训练数据等 11 个文件共 31,669,996 字节，全部逐文件下载并通过 SHA-256 校验，完成清单也已回读通过。[实际传输报告](../samples/opendota/ranked-official-20261112-1000/onedrive-verification.json)

OneDrive 内的位置为 `DOTA2_hero_selector/opendota/ranked-official-20261112-1000/`。当前环境的授权变量已注入并可用；可复用环境配置的保存和新任务重建恢复仍待界面确认及新任务验收。不能把本任务连接成功等同于新任务已经可以自动继承。

验证后已删除本任务解码生成的私有授权文件，避免进入环境发布快照；后续使用时由保存的变量重新恢复。当前没有需要更新到保管库的续期配置。

## 1. Mac 上完成一次授权

打开「终端」。有 Homebrew 时执行：

```bash
brew install rclone
rclone version
rclone config
```

云端固定为 rclone 1.76.0，建议 Mac 使用同版本。若没有 `brew`，可从[官方安装说明](https://rclone.org/install/#macos-installation)安装对应芯片的独立版本；不要为了传递凭据把配置粘到聊天里。

交互配置按下面选择；菜单编号可能随版本变化，优先认名称：

| 提示 | 选择 |
|---|---|
| New remote | `n` |
| name | `dota_onedrive` |
| Storage | `onedrive` / Microsoft OneDrive |
| client_id / client_secret | 分别直接回车，使用 rclone 默认应用 |
| region | 自己账号对应的区域；普通国际个人微软账号选 Microsoft Cloud Global |
| Edit advanced config? | `n` |
| Use web browser to automatically authenticate? | `y` |
| 微软浏览器登录 | 登录要存数据的个人账号，并核对授权提示 |
| OneDrive Personal or Business | 选择此项；随后选择自己的个人盘 |
| Is that okay / Keep this remote? | 盘正确时选 `y` |
| 配置菜单 | `q` 退出 |

可用 `rclone about dota_onedrive:` 检查容量，不需要列出全部个人文件。

## 2. 把授权直接复制到剪贴板

下面仅导出这个连接，编码为一行后送入 Mac 剪贴板，不在终端打印授权值。Base64 用于传递格式，不是加密；内容只粘贴到个人保管库的 Value。

```bash
set -o pipefail
rclone config show dota_onedrive | base64 | tr -d '\n' | pbcopy
```

不要把剪贴板内容贴到聊天、GitHub 文件、PR 或截图中。

## 3. 保存个人保管库

进入 **Settings → Codex Cloud → Personal vault → Add**：

- Type：`Environment variable`。
- Key：`ONEDRIVE_RCLONE_CONFIG_B64`。
- Value：粘贴刚才的剪贴板内容。
- Applies to：`Selected environments`，只选这个 DOTA 云环境，然后 Save。

这是程序必须读取的完整配置，不能选网络机密类型；网络机密只给程序占位符。**环境本身还必须请求同名个人变量**，只保存 Vault 值不会自动注入任务。[官方配置说明](https://learn.chatgpt.com/docs/environments/cloud-environments#supply-personal-values)

## 4. 固化可复用环境

普通任务无法直接保存环境编辑草稿或修改个人保管库。到 **Settings → Codex Cloud → Environments → 当前环境 → Edit**，在设置对话中要求：

> 按已经确认的个人 OneDrive 方案，为此环境新增可选个人环境变量 ONEDRIVE_RCLONE_CONFIG_B64，由我的 Personal vault 提供。保留已有安装脚本和 Start skill 的职责，在安装脚本末尾追加项目 cloud/onedrive-install.sh 的内容，在 Start skill 末尾追加 cloud/onedrive-start.md 的内容；读取这些文件时使用 data/opendota-official-20261112 分支。允许安装固定版本工具、检查配置并保存草稿。不要将授权写入发布快照或 Git，实际账户操作留到新任务。完成后给我差异供重新发布。

两个追加段：[安装配置](../cloud/onedrive-install.sh)、[启动配置](../cloud/onedrive-start.md)。安装段从固定 GitHub 提交下载两个程序并核对 SHA-256，独立保存到 `/workspace/.cloud-environment/onedrive-tools/`，不依赖新任务一定检出功能分支。不会覆盖原 Android/Java 配置。

审阅配置并 **Republish**。发布后新开任务，选择这个环境，再验证变量是否注入和 OneDrive 连接；当前旧任务不会因为重新发布而自动变成新版环境。[官方环境复用说明](https://learn.chatgpt.com/docs/environments/cloud-environments#reuse-and-update-saved-state)

## 5. 云端恢复、备份及验收

在新版环境的新任务运行：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py bootstrap
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py status
```

`status` 只返回连接是否成功、容量数值和是否需要更新保管库，不输出令牌、盘内文件或配置。确认剩余空间后，使用本批现有归档和训练目录：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py upload \
  --archive data/backups/20261112/ranked-official-20261112-1000.tar.zst \
  --training samples/opendota/ranked-official-20261112-1000 \
  --batch ranked-official-20261112-1000 \
  --report data/backups/20261112/onedrive-verification.json
```

新任务若没有这些本地数据，先从[已有 GitHub 原始备份](https://github.com/fenghanyue/DOTA2_hero_selector/tree/1b4b304ad5a13ea124257f363fcf36288eca6479/backups/20261112)按 README 恢复归档，把合并的 `.tar.zst` 放到命令中的 `data/backups/20261112/`。训练目录在数据分支，先执行 `git fetch origin data/opendota-official-20261112`，再用 `git archive FETCH_HEAD samples/opendota/ranked-official-20261112-1000 | tar -x` 恢复，不切换现有分支。

目的地固定在 `DOTA2_hero_selector/opendota/<batch>/`。程序只上传原始 `.tar.zst` 和白名单内的训练文件/报告，使用不可变文件检查；同批次内容变化时使用新的批次名。每个上传文件都下载并计算 SHA-256，全部通过后才写远端 `manifest.json` 及本地验证报告。没有设定无人值守采集计划。

## 6. 授权续期

rclone 会更新运行时配置；程序不会用相同的旧种子覆盖已续期的配置。输出 `vault_update_required=true` 表示运行时配置有变化，应把最新版本保存回个人保管库。当前任务没有自动回写 Vault 的接口：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py export-vault
```

该命令只写出仓库外的 `~/.config/dota2-hero-selector/personal-vault-value.txt`（权限 0600），不回显内容。用户在安全的文件界面复制其内容，更新 Vault 的同名值；不能把它传到聊天里。授权过期、撤销或需要新登录时，在 Mac 重新授权并重复第 2～3 步。[微软刷新令牌说明](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)

验收要分别记录：程序测试、微软实际授权、真实上传及回读、新环境重建恢复。仅有配置文件或已发布环境，都不能替代实际账户验证。
