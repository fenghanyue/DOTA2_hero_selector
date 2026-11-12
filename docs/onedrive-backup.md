# 个人 OneDrive：Mac 授权与云环境复用

2026-11-12：当前云任务已完成个人 OneDrive 的真实连接和首批备份验证。原始归档与训练数据等 11 个文件共 31,669,996 字节，全部逐文件下载并通过 SHA-256 校验，完成清单也已回读通过。[实际传输报告](../samples/opendota/ranked-official-20261112-1000/onedrive-verification.json)

OneDrive 内的位置为 `DOTA2_hero_selector/opendota/ranked-official-20261112-1000/`。用户随后在新任务验收确认：授权变量自动注入、格式有效，仓库外 0600 授权文件恢复成功；工具固化未通过。新任务只有原有 rclone 1.60.1，缺少专用脚本和固定的 rclone 1.76.0；临时补齐后连接成功，剩余约 579.18 GB，既有备份清单与 GitHub 一致、抽查文件 SHA-256 通过，无需更新保管库。这是用户提供的新任务实测结果；临时补装不计为工具自动恢复通过。

新任务恢复出的 `/workspace/.cloud-environment/install.sh` 和 `/workspace/.cloud-environment/start.md` 均缺少 OneDrive 追加段。原任务中已有相同追加段，但没有保存进可复用环境并重新发布；Promote 后授权设置可用，工具安装与启动配置仍需按第 4 节补齐。云端规则入口的旧缓存也仍待处理，读取远端默认分支规则不等于已更新发布快照。

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

当前普通任务只提供环境状态查询，没有保存环境编辑草稿、重新发布或修改个人保管库的工具。到 **Settings → Codex Cloud → Environments → 当前环境 → Edit**，在环境设置对话中粘贴：

> 修复已经确认的 OneDrive 工具固化：保留现有仓库、网络设置、已能自动注入的 ONEDRIVE_RCLONE_CONFIG_B64 变量及其个人保管库绑定。读取 fenghanyue/DOTA2_hero_selector 的固定提交 dfba08df62b12ecf292dae0e07e94c55d315bc61，将 cloud/onedrive-install.sh 追加到当前环境安装脚本，将 cloud/onedrive-start.md 追加到 Start skill，已有相同段落时避免重复。保留原有 Android/Java 和项目启动职责。将这两个字段保存到可重新发布的环境配置草稿，不能只修改当前任务的同名文件。允许安装固定版本工具并核对来源、校验和及脚本语法；不要恢复个人授权或进行账户操作。展示实际配置字段、追加差异、检查结果和草稿保存状态，供我重新发布。若缺少保存工具，明确报告未保存。

两个追加段的固定来源与 SHA-256：

| 追加到 | 固定来源 | SHA-256 |
|---|---|---|
| 安装脚本 | [cloud/onedrive-install.sh](https://github.com/fenghanyue/DOTA2_hero_selector/blob/dfba08df62b12ecf292dae0e07e94c55d315bc61/cloud/onedrive-install.sh) | `6b000f09c0d99b67b32e0fea323d6610537753336cf153a333a3e619f64e001e` |
| Start skill | [cloud/onedrive-start.md](https://github.com/fenghanyue/DOTA2_hero_selector/blob/dfba08df62b12ecf292dae0e07e94c55d315bc61/cloud/onedrive-start.md) | `57baad9cd23fac9fca79dd1d32a0b200eb3ba1e93f9508cc529511e07896c756` |

安装段从固定 GitHub 提交下载两个程序并核对 SHA-256，独立保存到 `/workspace/.cloud-environment/onedrive-tools/`，不依赖新任务一定检出功能分支。不会覆盖原 Android/Java 配置。原任务可供比对的差异是安装脚本末尾新增 37 行、启动说明末尾新增 11 行；这些临时文件本身不能证明环境草稿已经保存。

确认环境草稿保存了两个字段、工具准备完成，再审阅并 **Republish**。只提升变量设置或只在普通任务补装工具，都不算这一步完成。发布后新开任务，选择这个环境，按第 5 节先验收工具再恢复授权；当前旧任务不会因为重新发布而自动变成新版环境。[官方环境复用说明](https://learn.chatgpt.com/docs/environments/cloud-environments#reuse-and-update-saved-state)

## 5. 云端恢复、备份及验收

在新版环境的新任务，先检查恢复出的安装脚本、启动说明是否含追加段，并核对 `/workspace/.cloud-environment/onedrive-tools/sources.json` 记录的来源和程序哈希。以下工具必须在任何临时补装之前就已存在：

```bash
test -f /workspace/.cloud-environment/onedrive-tools/install_rclone.py
test -f /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py
test -f /workspace/.cloud-environment/onedrive-tools/sources.json
"$HOME/.local/share/dota2-hero-selector/bin/rclone" version
```

固定路径的版本应为 1.76.0，不能用系统已有的 1.60.1 代替。任一缺失时，先记录工具固化未通过；随后即使临时安装并连接成功，也保留该失败结论。工具恢复通过后，再运行：

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
