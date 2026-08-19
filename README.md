# Claude Code portable config

用于在 WSL、原生 Linux 和 macOS 主机上恢复核心工作环境的 Claude Code 便携配置；peon-ping profile 另提供原生 Windows 迁移入口。

CC-Switch 继续负责 Provider、API、Base URL、代理、凭证和模型路由。

## 推荐使用

### 主力机器同步到 Git

在 `~/cc-config` 中启动 Claude Code，然后：

> 请按 AGENT_SYNC.md 同步当前 Claude Code 配置到 cc-config。

### 新机器从 Git 恢复

克隆仓库后，在 `~/cc-config` 中启动 Claude Code，然后：

> 请按 AGENT_SETUP.md 帮我完成这台机器的 Claude Code 环境迁移。

- `AGENT_SYNC.md`：主力机器 → Git
- `AGENT_SETUP.md`：Git → 新机器

## 新机器

```bash
git clone https://github.com/xcg124110023416/cc-config.git ~/cc-config
cd ~/cc-config
./install.sh
./doctor.sh
claude
```

也可以让 Agent 按恢复协议自动补齐（`AGENT_SETUP.md`）：

> 在 Claude Code 中发起：请按 AGENT_SETUP.md 帮我完成这台机器的 Claude Code 环境迁移。

Agent 会执行 install → doctor → 查官方文档补缺失依赖 → 复验 → 输出剩余人工认证清单。

安装器使用：

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
```

如果设置了 `CLAUDE_CONFIG_DIR`，则使用该目录；否则回退到 `~/.claude`。

## 恢复内容

本仓库恢复：

- 全局 `CLAUDE.md`
- 便携 Skills
- 便携 Claude 设置
- Claude HUD / statusLine
- 托管插件
- 便携 MCP Servers
- 便携 Serena hooks
- 按宿主系统选择的 peon-ping profile
- CC-Switch Common Config
- CC-Switch 兼容 wrapper

当前便携 MCP Servers：

- `codegraph`
- `serena`
- `sciverse`
- `mcp-ssh`

当前便携 hooks：

- `SessionStart -> serena-hooks activate --client=claude-code`
- `SessionEnd -> serena-hooks cleanup --client=claude-code`

## 配置归属

`settings.portable.json` 是便携 Claude 行为的唯一真源。

CC-Switch 管理：

- Provider
- API / 认证
- Base URL
- 代理
- 模型选择与路由
- `ENABLE_TOOL_SEARCH`

本仓库刻意不保存 Provider 凭证或 API Key。

## CC-Switch wrapper

`install.sh` 会把 `~/cc-config/bin` 放到 PATH 中真实 CC-Switch 二进制之前。

官方二进制不会被修改。

wrapper 会在以下操作后恢复便携设置和 hooks：

```bash
cc-switch use <provider>
cc-switch provider switch <provider>
```

也会在退出交互式 CC-Switch TUI 后恢复。

普通 CC-Switch 命令原样透传。

绕过 wrapper：

```bash
~/.local/bin/cc-switch
```

## 插件

托管的插件集合保存在 `plugins.json`。

当前插件：

- `claude-hud@claude-hud`
- `andrej-karpathy-skills@karpathy-skills`
- `sciverse@sciverse`
- `obsidian@obsidian-skills`

插件缓存不进入 Git。

## MCP

便携 MCP 定义保存在 `mcp.portable.json`。

通过官方 Claude MCP CLI 恢复。

本仓库不复制完整的 `.claude.json`。

SciVerse 凭证不迁移。新机器需要自备：

```text
~/.config/sciverse/token
```

## Hooks

便携 hooks 保存在 `hooks.portable.json`。

它们会合并进已有的 Claude 设置，且不会删除无关的本机 hooks。

## peon-ping

peon-ping 属于“仓库管理、宿主选择”的 profile，不放进全平台共享的
`hooks.portable.json`，也不依赖已有 `settings.json` 快照才能恢复。
`install.sh` 会检测主机、显示将选择的 profile，经确认后安装固定版本且
SHA-256 校验通过的上游运行时，并在最后重对账 hooks。

支持矩阵：

| 主机 | Profile | 执行与音频后端 |
|---|---|---|
| WSL2 + WSLg/Pulse | `wsl-native` | WSL 内的 `peon.sh`，强制 Linux `pw-play` / `paplay` / `ffplay` 等链路；不调用 Windows 可执行文件 |
| 原生 Linux / Ubuntu | `linux` | Linux `peon.sh` 与本机音频播放器 |
| macOS | `macos` | Unix `peon.sh` 与 `afplay` |
| 原生 Windows | `windows` | Windows PowerShell `peon.ps1` |
| 其他或显式关闭 | `none` | `NOT_APPLICABLE` |

自动检测可用 `CC_CONFIG_PEON_PROFILE` 覆盖：

```bash
CC_CONFIG_PEON_PROFILE=linux ./install.sh
CC_CONFIG_PEON_PROFILE=none ./install.sh
```

权威 profile 声明位于 `profiles/peon-ping/profile.json`，管理器是
`scripts/manage-peon-profile.py`。上游 runtime、sound packs、用户
`config.json`、`.state.json` 与日志不进入 Git；迁移时保留已有配置和状态。

profile hooks 由管理器按目标机生成，会移除旧的 peon-only handler，保留
Serena 和其他无关 hooks。`install.sh` 与 CC-Switch wrapper 都会在可能重写
`settings.json` 的操作后重新对账 profile，因此 peon 不再依赖一次性的本机
hook 快照。`doctor.sh` 会检查 profile、runtime、音频后端、handler 数量和
WSL 中残留的 PowerShell peon 命令。

手动查看或修复：

```bash
python3 scripts/manage-peon-profile.py detect
python3 scripts/manage-peon-profile.py status
python3 scripts/manage-peon-profile.py install
python3 scripts/manage-peon-profile.py reconcile
```

安装需要联网下载锁定的上游源码和默认 packs；普通 doctor/status 不联网，
也不会播放声音。新安装或 profile 更新后重启 Claude Code，使 hooks 全量生效。

## doctor

运行：

```bash
./doctor.sh
```

检查必需和可选依赖。

项目专属检查：

```bash
./doctor.sh --project /path/to/project
```

`doctor.sh` 只检查依赖，不安装软件。

## 从主力机器更新

```bash
cd ~/cc-config
./update.sh

git status
git add .
git commit -m "Update Claude Code config"
git push
```

也可以让 Agent 按同步协议处理：

> 在 Claude Code 中发起：请按 AGENT_SYNC.md 同步当前 Claude Code 配置到 cc-config。

Agent 会盘点差异、按类型更新 portable manifest、验证并提交推送。

需要时直接编辑这些便携 manifest：

```text
settings.portable.json
hooks.portable.json
mcp.portable.json
plugins.json
```

然后运行：

```bash
./install.sh
./doctor.sh
```

再提交。

## 刻意不迁移的内容

以下内容刻意保持本机：

- Provider 凭证
- API Key / token
- OAuth 状态
- CC-Switch 数据库
- 完整 `settings.json`
- 完整 `.claude.json`
- sessions
- history
- projects
- caches
- 插件缓存
- 其他机器相关的运行状态

"不同步"不等于"应该删除"。

## 恢复

重新应用便携配置：

```bash
cd ~/cc-config
./install.sh
```

绕过 CC-Switch wrapper：

```bash
~/.local/bin/cc-switch
```
