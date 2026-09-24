# Codex 主力机 → Git 同步

在运行 Codex 的当前系统中，从仓库根目录执行：

```powershell
python scripts/codex-config.py sync  # Windows 也可用 py -3；Linux / WSL 使用 python3
```

这条命令只盘点全局指令、Skills、白名单设置和仓库列出的 MCP，
不会反向导入本机文件。依次判断差异是有意新增、修改、停用，
还是当前系统缺少依赖；不要把 WSL 与 Linux/Windows 上的缺失视为删除意图。

- 全局指令：只维护 `codex/AGENTS.md` 对应的托管块；全局文件的其余内容
  保留在本机。项目自己的指令维护在项目的 `AGENTS.md`。
- Skills：从用户 `~/.agents/skills/<name>` 比较仓库 `skills/<name>`；
  审核后更新仓库内容。排除插件缓存、机器专属路径、凭证和生成状态。
- 偏好：仅修改 `codex/portable.json` 中 `settings` 的便携白名单项。
  模型、Provider、API、权限策略和本机绝对路径仍在本机 `config.toml`。
- MCP：`mcp.portable.json` 是跨客户端定义来源；
  `codex/portable.json` 只选择安装到 Codex 的服务器。Serena 的
  `--context=claude-code` 会在 Codex 安装时改成 `--context=codex`；
  SciVerse 用本机 Python 与本机凭证启动。新增项须确认命令和依赖在
  目标操作系统中同样有效；不要导入本机认证数据。
- Hooks：Codex 专用声明在 `codex/hooks.json`，仅安装本机具备
  `serena-hooks` 的命令。它与 Claude 的 `hooks.portable.json` 分开。
- Plugins：Claude `plugins.json` 的 ID 不对应 Codex 插件；先确认 Codex
  marketplace 与插件兼容性，再单独设计便携清单，不直接复制已有插件。

修改后依次运行 `python3 scripts/audit-portable.py . --settings-validator
scripts/merge-settings.py`（Windows 用 `python` 或 `py -3`）、仓库测试、
`python3 scripts/codex-config.py install` 与 `doctor`（Windows 用 `python` 或 `py -3`），
核对 `git diff` 再提交推送。
禁止提交 `config.toml` 完整快照、`auth.json`、会话历史、缓存、机器绝对路径
或私密环境变量。仓库清单中删除一项表示停止在新机器安装，不自动卸载
已有机器的组件。
