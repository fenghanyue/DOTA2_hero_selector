# 个人 OneDrive：云端备份与恢复

2026-11-13：用户新任务已验收工具、rclone 1.76.0 和个人授权自动恢复。本次新增现成的 `upload`、`verify`、`restore` 命令，在独立批次实测上传、完整回读、下载到新目录及重复上传，均通过大小和 SHA-256 校验。每阶段 11 个文件、31,669,996 字节；恢复落盘后的文件也重新核对通过。[整条存取流程实测报告](../samples/opendota/ranked-official-20261112-1000/onedrive-storage-e2e-20261113.json)

原始数据位置仍为 `DOTA2_hero_selector/opendota/ranked-official-20261112-1000/`，最初的[上传报告](../samples/opendota/ranked-official-20261112-1000/onedrive-verification.json)保持不变。集成验收批次 `storage-e2e-20261113T054634Z-singlepart` 是这批数据的相同副本，不新增比赛，不额外计入 830 场训练样本。早期失败测试留下的两个零字节批次已清理。

当前环境中的预认证下载和分块上传曾返回 401，根因尚未确定。用户已确认临时方案：上传继续用 rclone，但对不超过 **200 MiB** 的文件走正式 Graph 单次上传；回读校验和恢复显式使用 `--read-method graph-contentstream`。[contentStream 属于 beta，微软不支持用于生产应用](https://learn.microsoft.com/en-us/graph/api/driveitem-get-contentstream?view=graph-rest-beta)。标准读取仍为默认值，不静默切换到 beta。

环境已由用户重新发布；用户随后在独立新任务实测工具自动恢复、授权恢复、原备份校验恢复、新批次上传校验和新批次恢复，五项全部通过。[新任务验收记录](../samples/opendota/ranked-official-20261112-1000/onedrive-published-acceptance-20261113.json)据用户粘贴的实测总结保存，原始报告文件位于该独立任务，本任务未访问或重跑。新批次 `published-check-20261113T151026180709` 仍为现有数据副本，新增比赛为 0。当前约定的临时存取方案已验收完成；第 4、7 节保留为后续工具升级时的发布及验收步骤。

运行时授权位于仓库外、配置权限 0600、目录 0700。新任务正常续期后报告 `vault_update_required=true`，不表示授权恢复失败；个人保管库的后续同步仍由用户在界面完成，工具不能自动回写。

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

## 4. 更新可复用环境

普通任务没有环境草稿保存、发布或保管库写入工具。当前任务文件已更新，不等于已保存环境草稿。到 **Settings → Codex Cloud → Environments → 当前环境 → Edit**，在设置对话中粘贴：

> 更新已确认的 OneDrive 临时存取方案。读取 fenghanyue/DOTA2_hero_selector 固定提交 7c4546a61b82c7fcd42fb8ddf1ad05b90769ff55 中的 cloud/onedrive-install.sh 和 cloud/onedrive-start.md。用新片段替换 install_script、start_skill 中已有的 OneDrive 追加段，避免重复；安装脚本中如有本地 start.md 镜像生成逻辑，也同步更新镜像。保留其余规则同步、Android/Java、仓库、网络及 ONEDRIVE_RCLONE_CONFIG_B64 的个人绑定和 optional 设置。允许运行安装片段，核对来源、SHA-256、Bash 语法，以及帮助中的 upload、verify、restore 和 --read-method graph-contentstream。准备阶段不恢复授权、不操作 OneDrive 账户。将字段保存到可重新发布的环境配置草稿，回读确认已保存，展示实际字段差异和检查结果，供我点击 Republish。不能只修改当前任务文件；缺少保存工具时明确报告未保存。

两个追加段的固定来源与 SHA-256：

| 追加到 | 固定来源 | SHA-256 |
|---|---|---|
| 安装脚本 | [cloud/onedrive-install.sh](https://github.com/fenghanyue/DOTA2_hero_selector/blob/7c4546a61b82c7fcd42fb8ddf1ad05b90769ff55/cloud/onedrive-install.sh) | `6eacf4e42e0310e5e4d77ec9c9675dc0fc53de3e92bb860168ae752a234a2675` |
| Start skill | [cloud/onedrive-start.md](https://github.com/fenghanyue/DOTA2_hero_selector/blob/7c4546a61b82c7fcd42fb8ddf1ad05b90769ff55/cloud/onedrive-start.md) | `2fd9251f9d965e852f7ed4cc9a6e4bc8f8c6df30901537d71258526e03931885` |

安装段将程序固定到提交 `8fc440417014c3a249ad5186a0c3f5b826c571ab`，`onedrive_backup.py` 的 SHA-256 为 `3bbb9dc61f8e1e25cc2432514be128265ebc833940760a3bde09e130c0367408`。两个程序独立保存在 `/workspace/.cloud-environment/onedrive-tools/`，来源记在 `sources.json`；即使项目仍检出尚未包含脚本的 `main`，已发布工具也可使用。安装片段对子进程移除授权变量，不解码授权。

确认实际草稿字段保存、工具准备完成后，再审阅并 **Republish**。发布后用该环境新开任务按第 7 节验收；现有任务保留自己的文件状态。[官方环境复用说明](https://learn.chatgpt.com/docs/environments/cloud-environments#reuse-and-update-saved-state)

## 5. 云端保存、校验和取回

先检查工具，不临时补装掩盖恢复失败：

```bash
test -f /workspace/.cloud-environment/onedrive-tools/install_rclone.py
test -f /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py
test -f /workspace/.cloud-environment/onedrive-tools/sources.json
"$HOME/.local/share/dota2-hero-selector/bin/rclone" version
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py --help
```

固定 rclone 应为 1.76.0，脚本应有 `upload`、`verify`、`restore`。核对来源与第 4 节一致后恢复授权：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py bootstrap
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py status
```

`status` 只报告连接、容量和是否需要更新保管库。已有归档和训练目录时，上传并逐文件回读校验；下例为现有批次，新采集时替换输入路径和批次名：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py upload \
  --archive data/backups/20261112/ranked-official-20261112-1000.tar.zst \
  --training samples/opendota/ranked-official-20261112-1000 \
  --batch ranked-official-20261112-1000 \
  --report data/backups/20261112/onedrive-latest-upload.json \
  --read-method graph-contentstream
```

同批次已有完整且相同的备份时，`upload` 只重新校验并报告 `already_complete=true`，不会重写完成清单。内容不同则拒绝覆盖，要求新批次。只读验证远端整个批次：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py verify \
  --batch ranked-official-20261112-1000 \
  --report data/backups/20261112/onedrive-latest-verify.json \
  --read-method graph-contentstream
```

新任务没有任何本地数据时，可直接从 OneDrive 下载到一个尚不存在的目录：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py restore \
  --batch ranked-official-20261112-1000 \
  --output data/restored/ranked-official-20261112-1000 \
  --read-method graph-contentstream
```

结果包含 `raw/*.tar.zst`、`training/`、`manifest.json` 和 `restore-report.json`。原始压缩包不自动解压；训练数据可直接从 `training/matches.jsonl` 使用。恢复中途失败时临时目录被清理，目标目录不会留下不完整结果；已有目标不覆盖，应换新的输出路径。

目的地固定在 `DOTA2_hero_selector/opendota/<batch>/`。只上传原始归档和训练白名单文件，文件使用不可变检查、完成清单最后上传。临时上传模式单文件超过 200 MiB 时会在网络操作前停止，应拆小采集批次；不自动改用失败的上传会话。Graph GET 对部分暂时错误最多尝试三次，401/403 不反复重试；长时间限流会要求稍后重试。失败时不生成成功报告。

采集、导出、打包、上传、恢复均在云任务中执行。采集脚本不会自动上传，也没有无人值守或定时计划。原始采集目录需先打包为 `.tar.zst`，按实际目录使用已有 `tar`、`zstd`。OneDrive 不可用时，最初批次仍有[GitHub 原始归档](https://github.com/fenghanyue/DOTA2_hero_selector/tree/1b4b304ad5a13ea124257f363fcf36288eca6479/backups/20261112)可按其 README 恢复。

## 6. 授权续期

rclone 会更新运行时配置；程序不会用相同的旧种子覆盖已续期的配置。输出 `vault_update_required=true` 表示运行时配置有变化，应把最新版本保存回个人保管库。当前任务没有自动回写 Vault 的接口：

```bash
python3 /workspace/.cloud-environment/onedrive-tools/onedrive_backup.py export-vault
```

该命令只写出仓库外的 `~/.config/dota2-hero-selector/personal-vault-value.txt`（权限 0600），不回显内容。用户在安全的文件界面复制其内容，更新 Vault 的同名值；不能把它传到聊天里。授权过期、撤销或需要新登录时，在 Mac 重新授权并重复第 2～3 步。[微软刷新令牌说明](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)

## 7. 重新发布后的新任务验收

选择重新发布的环境新开任务，先核对第 4 节的来源、哈希和第 5 节的命令帮助；工具须未经补装就存在。随后恢复授权，用 `verify` 读取现有批次，再用 `restore` 下载到新目录，检查实际文件和报告。需要验证新任务上传时，可从已恢复的数据生成独立验收批次，再执行 `upload → verify → restore`，不采集新比赛、不覆盖原批次。

本版验收已经完成：50 项离线测试、开发任务真实存取通过；用户重新发布后，在独立新任务完成全部五项实测，11 个数据文件共 31,669,996 字节，恢复后的 13 个文件（含两份元数据）均记录并核对大小和 SHA-256。后续升级仍须分别记录代码测试、当前任务真实存取、草稿保存、重新发布和新任务验收，不能相互替代。原 401 根因仍未确定；临时 beta 方案的长期兼容性未获保证。
