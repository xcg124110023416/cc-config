# Codex 新设备恢复

本协议只操作当前系统内运行的 Codex：原生 Windows、WSL 内的 Linux Codex、
原生 Linux（包括双系统）及 macOS 相互独立。不会调用另一系统的程序或配置。
原生 Windows 的 Codex CLI 与桌面端共用 `%USERPROFILE%\.codex`，无需分别迁移。

## 路径

| 平台 | 全局配置与指令 | 用户 Skills |
| --- | --- | --- |
| 原生 Windows | `%USERPROFILE%\.codex\config.toml`、`AGENTS.md` | `%USERPROFILE%\.agents\skills\` |
| WSL Linux / 原生 Linux / macOS | `${CODEX_HOME:-$HOME/.codex}/config.toml`、`AGENTS.md` | `$HOME/.agents/skills/` |

Windows 同样可以设置 `CODEX_HOME` 覆盖默认的 Codex 目录。
在 WSL 里只使用 Linux 用户目录和 Linux 版 Codex；如果当前 `codex`
来自 `/mnt/c/...` 等 Windows 挂载路径，安装器会跳过 Codex CLI 操作；
其他 MCP 依赖也只接受当前系统的程序。须先在 WSL 内安装原生 CLI 后
重新运行，不会调用 Windows 版程序。
项目指令在项目根目录的 `AGENTS.md`，项目配置可在可信项目的
`.codex/config.toml`。项目目录与用户全局目录不要混用。

## 安装和检查

在当前系统中克隆此仓库，然后运行：

```powershell
# 原生 Windows PowerShell（若只有 py 启动器，可用 py -3）
python scripts/codex-config.py install
python scripts/codex-config.py doctor
```

```bash
# WSL Linux、原生 Linux 或 macOS
python3 scripts/codex-config.py install
python3 scripts/codex-config.py doctor
```

`install` 会列出目标路径并询问确认。自动化终端可添加 `--yes`。
安装器将便携指令合并到全局 `AGENTS.md` 的托管块，将仓库 Skills
复制到用户 Skills 目录，并从固定提交、经 SHA-256 校验的上游来源恢复
Karpathy、Obsidian、Matt Pocock 和 SciVerse Skills。Matt 的
`setup-matt-pocock-skills` 在 Codex 副本中优先编辑 `AGENTS.md`；
开发中及明确 Claude 专用的 Skills 不装。它仅在缺少该键时写入便携偏好，
通过 `codex mcp` 注册**当前机器已具备依赖**的服务器。重启 Codex
以重新发现 Skills。
Codex 内置 TUI 状态栏的项目与顺序保存在 `codex/portable.json` 的
`tui.status_line`；安装器只在目标配置缺少该字段时补入，保留本机已有布局。

Serena 与 peon-ping 的 Codex hooks 合并到同一个 `hooks.json`，不会与
`config.toml` 内联 hooks 混用。若已有内联 hook 定义，安装器保留它们并
报告冲突，不擅自转换；`[hooks.state]` 信任记录不算内联定义。新命令 hook
可能需要在 Codex 的 `/hooks` 中审核并信任。
Windows 上 peon-ping hook 使用无需引号的本机短路径，兼容 Codex 选用的
PowerShell 或 cmd。若文件系统无法提供安全的短路径，安装器会报告缺口，
不会写入一个会在发送消息时报错的 hook。
Windows peon 脚本仅在启动的子进程中使用 `ExecutionPolicy Bypass`，并按 UTF-8
读取配置与声音清单，兼容默认限制脚本运行的 PowerShell 设置。

peon-ping 使用独立的 `$CODEX_HOME/cc-config-peon-ping/` 运行时，不依赖
Claude Code 的安装。安装器按本机选择 `wsl-native`、`linux`、`macos` 或
`windows` profile，校验固定上游源码、5 个默认包的 manifest 和每个声音
文件的哈希。可用 `CC_CONFIG_PEON_PROFILE=none` 显式禁用；未具备本机音频
后端时 `doctor` 会报告缺口，不改用另一系统的播放器。新安装默认启用
`task.acknowledge`，发送消息会播放确认音；已有本机配置的该开关保持原值。
上游 7 个 peon Skills 中，`peon-ping-config` 和 `peon-ping-toggle` 已适配
Codex 独立运行时；其余 5 个涉及训练记录、会话命令或 Claude 驱动的声音包
创作流程，暂不迁入。

若安装了 CC-Switch，安装器还会备份并合并 Codex Common Config：
保留本机已有字段，只加入缺少的便携偏好与**本机依赖和凭证已齐**的 MCP，
并移除过时的顶层 `disable_response_storage`。Provider、API、模型路由
仍由 CC-Switch / 用户自行管理；用于 Codex 的 Provider 需自行确认已附加
Common Config，切换后运行 `doctor` 对账。

已有的同名本机 Skill、MCP 和偏好（包括状态栏）若内容不同，会被保留并报告，不自动覆盖。
已由本安装器管理且未在本机修改的 Skill 可以安全更新。修改配置前会备份
受影响的文件至 `$CODEX_HOME/backups/cc-config-codex-*`。再次运行安装器可以
补齐新装的依赖。`doctor` 只检查，不安装或登录；缺少可选依赖时会报告差异。

## 本机需要单独处理

- 每个系统分别完成 Codex 登录；不要复制 `auth.json`、会话、历史、缓存。
- 每个系统分别安装需要的 CLI（如 `codegraph`、`serena`、
  `serena-hooks`、`sciverse-mcp-server`、`npx`）。如果没有相应依赖，
  对应 MCP 或 hook 会跳过，之后重新运行 `install`。
- SciVerse 使用本机的 `~/.config/sciverse/token`；在 Windows 上为
  `%USERPROFILE%\.config\sciverse\token`。如果设置 `XDG_CONFIG_HOME`，
  则从它的 `sciverse/token` 读取。仓库和 Codex 配置都不保存该凭证。
- Claude Code 的插件 ID 不能直接复制到 Codex。HUD 和没有 Codex
  实现的插件不迁移；可复用的上游 Skills、SciVerse MCP 与 peon-ping
  Codex adapter 按上述方式独立恢复。
- CC-Switch 的 Provider / API / Base URL / 模型路由不迁移，也不读取
  旧机器的数据库或凭证。Codex Common Config 只管理上述便携片段。
- 某些 Skill 的外部依赖和字体仍需在每个系统分别安装；`doctor` 检查
  便携配置是否就位，不保证每个第三方 Skill 的全部外部功能可用。

在主力机同步有意的变化，按 [CODEX_SYNC.md](CODEX_SYNC.md) 执行。
