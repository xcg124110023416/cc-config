# AGENT_SETUP.md

新机器 Claude Code 环境恢复协议。本文件是给 Claude Code / Agent 的操作规则，
不是安装教程——刻意不保存各依赖的安装命令，因为它们会过时。

## 触发

用户在新机器上克隆仓库后发起：

> 请按 AGENT_SETUP.md 帮我完成这台机器的 Claude Code 环境迁移。

```bash
git clone https://github.com/xcg124110023416/cc-config.git ~/cc-config
cd ~/cc-config
```

## 核心流程

1. **识别环境** —— 发行版（`/etc/os-release`）、是否 WSL（`uname -r` 含 microsoft）、
   `CLAUDE_CONFIG_DIR` / `$HOME`、现有依赖（`command -v`）。
2. **阅读仓库** —— `README.md`（配置归属与刻意不迁移内容）、
   `settings.portable.json` / `hooks.portable.json` / `mcp.portable.json` / `plugins.json`。
3. **部署便携配置** —— 运行 `./install.sh`（备份、安全合并、symlink、插件 / MCP / hooks 恢复）。
4. **检测缺口** —— 运行 `./doctor.sh`。doctor.sh 是依赖缺口的主要事实来源；
   同时检查 `install.sh` 输出以及 MCP / hooks 的最终状态。不要仅以 doctor 的退出码判断迁移是否完成。
5. **分类缺失项**：

   | 类别 | 处理 |
   |---|---|
   | 核心依赖缺失 | 进入补齐循环（步骤 6） |
   | 可选依赖缺失（如 MinerU） | 询问用户是否需要；不需要则保留 MISSING |
   | 环境相关组件 | 按匹配 profile 或当前官方支持方式尝试恢复；不适用则标记 SKIPPED / NOT_APPLICABLE |
   | 必须人工认证（Provider / API / Token） | 停止自动处理，进入最终清单 |

6. **补齐核心依赖** —— 对每个可自动处理的缺失项：查最新官方文档 / 官方 GitHub / 官方发布说明，
   用当前官方推荐且适合当前系统的方式安装；安装前 `command -v` 判断避免重复；
   失败先分析原因（网络 / 权限 / 版本 / 来源），不盲目换源。
7. **复验** —— 重新运行 `./install.sh && ./doctor.sh`。
8. **循环** —— 重复 5–7，直到满足收敛条件：核心依赖已恢复；环境相关组件已恢复或明确标记
   `SKIPPED` / `NOT_APPLICABLE`；剩余只有人工认证或用户明确不需要的可选依赖。
9. **收敛** —— 输出最终状态分类与人工认证清单。

## 依赖补齐原则

- doctor.sh 是依赖缺口的主要事实来源；同时检查 `install.sh` 输出与 MCP / hooks 的最终状态。
  不要把所有依赖的安装逻辑重复实现到 shell 里。
- 安装方式一律以迁移当天的官方资料为准，优先官方文档、官方 GitHub、官方发布说明。
- 适配当前环境（Ubuntu / WSL / 其他 Linux 可能有差异）。
- 已安装且可用的依赖直接跳过。
- 不把"本次学到的安装命令"写回仓库固化。
- 基础工具（git / python3 / node / claude / cc-switch）缺失时同样走官方渠道补齐，或提示人工。

## 必须人工处理（禁止自动迁移）

以下内容不要尝试从旧机器、Git 或其他文件自动迁移：

- CC-Switch Provider / API Key / Base URL / 模型路由
- API Key / Token / OAuth
- SciVerse token

可以：帮用户打开对应官方配置流程、执行官方登录命令、校验配置是否有效。
禁止：搜索旧 Token、复制旧凭证、打印环境变量、读取并回显敏感值、把凭证写进仓库。

## CC-Switch

- doctor 或实际运行发现 Provider 未配置 → 提示用户完成 Provider / API / Base URL 等配置。
- 可以校验配置是否有效。
- 不要从备份、数据库或其他机器自动提取 Provider 凭证。

## 环境相关组件

某些能力依赖宿主系统，不能像普通 Plugin / MCP 一样直接复用同一套配置。

当前已知组件：

- peon-ping

Agent 恢复时：

- 主动尝试恢复该能力；
- 优先复用与当前系统匹配的已有 profile；
- profile 不匹配时，查询迁移当天最新官方支持方式动态部署；
- 不把机器用户名、绝对路径等本机信息写回 Git；
- 当前系统不支持时标记 `SKIPPED` / `NOT_APPLICABLE`；
- 涉及 sudo、跨系统安装或明显系统修改时再向用户确认。

当前已有：

```text
profiles/wsl/peon-ping/
```

具体 WSL 部署细节见该 profile 自己的 README。

## 验证

每完成一轮修复都重新运行：

```bash
./install.sh
./doctor.sh
```

完成前确认 `claude mcp list` / `claude mcp get` 中的 portable MCP 已注册，
并确认 portable hooks 已合入当前 settings。

最终目标不是 doctor 全绿，而是明确分类：

- 核心环境已完成
- 可选依赖缺失（如 MinerU 可保留 MISSING，不阻塞）
- 必须人工认证
- 环境相关组件不适用 / 未部署（SKIPPED / NOT_APPLICABLE）

## 安全

- 不同步完整 `.claude.json` / `settings.json`
- 不复制 CC-Switch DB
- 不读取后打印真实 Token / API Key；不使用 `env` / `printenv` 调试
- 不把临时配置或备份加入 Git
- 不改动与迁移无关的 sessions / history / projects / cache
- 配置修改尽量走现有 `install.sh`、官方 CLI 和安全 merge 工具
