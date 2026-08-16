# Claude Code portable config

用于在新 WSL / Ubuntu / Linux 机器上恢复核心工作环境的 Claude Code 便携配置。

CC-Switch 继续负责 Provider、API、Base URL、代理、凭证和模型路由。

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
- CC-Switch Common Config
- CC-Switch 兼容 wrapper

当前便携 MCP Servers：

- `codegraph`
- `serena`
- `sciverse`

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

peon-ping 是迁移时希望恢复的一项环境能力，但具体部署方式取决于目标系统。

仓库目前保存了一套已验证的 WSL 模板：

```text
profiles/wsl/peon-ping/
```

在 WSL 环境中可以复用该模板。

如果目标环境不适用现有 WSL 模板，Agent 不应强行套用，
应查询当前官方支持方式并根据当前受支持环境动态配置。

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
