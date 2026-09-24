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
复制到用户 Skills 目录，并仅在缺少该键时写入便携 Codex 偏好。
它通过 `codex mcp` 注册**当前机器上已经具备依赖**的服务器；
有 `serena-hooks` 时合并 Codex 专用 hooks。重启 Codex 以重新发现 Skills。

已有的同名本机 Skill、MCP 和偏好若内容不同，会被保留并报告，不自动覆盖。
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
- Claude Code 的 `plugins.json`、HUD、CC-Switch 和 peon-ping profile
  不会被装进 Codex。Codex 插件须按其自己的 marketplace 和插件 ID 安装。
- 某些 Skill 的外部依赖和字体仍需在每个系统分别安装；`doctor` 检查
  便携配置是否就位，不保证每个第三方 Skill 的全部外部功能可用。

在主力机同步有意的变化，按 [CODEX_SYNC.md](CODEX_SYNC.md) 执行。
