# AGENT_SYNC.md

主力机器 → Git 的持续同步协议。本文件是给 Claude Code / Agent 的操作规则，
不是安装教程，也不重新实现复杂的同步脚本。

## 触发

在主力机器上向 Agent 发起：

> 请按 AGENT_SYNC.md 同步当前 Claude Code 配置到 cc-config。

核心原则：本协议管理的是"期望以后跨机器恢复的状态"，
而不只是不断往 Git 里增加内容——删除 / 停用同样是同步的一部分。

## 一、识别变化

先盘点当前机器与仓库的差异，不假设所有本机变化都应该进入 Git。
差异分三种，逐一判断是否是用户的有意改变：

- 本机有、仓库没有 → 可能是**新增**；
- 仓库有、本机没有 → 可能是**删除 / 停用**；
- 两边都有但内容不同 → 可能是**修改**。

不要默认任何差异都应该同步。先确认意图，再决定是否写入仓库。

检查来源：

- `git status` / `git diff`
- `./update.sh` 的检测结果
- Claude 当前 Plugins（官方 `claude plugin list --json`）
- Claude 当前 MCP（官方 `claude mcp list` / `claude mcp get`）
- portable hooks / portable settings
- Skills / CLAUDE.md / agents / commands / rules / output-styles 等仓库管理内容
- 必要时环境相关组件

## 二、按类型同步

### Skills / CLAUDE.md / agents / commands / rules / output-styles

- 优先利用现有 `update.sh` 的检测与安全导入。
- 比较仓库管理项与主力机状态：
  - 仓库没有、本机有 → 按 update.sh 检测与安全导入；
  - 仓库有、本机被用户有意删除 → 允许同步这个删除；
  - 两边都有但内容不同 → 判断后更新。
- 不要误删仅仅因为暂时不可见、路径异常或环境不适用的内容。
- 插件缓存、第三方生成缓存、机器运行状态 → 不导入。

### Plugins

比较当前已启用插件与 `plugins.json`，按三种状态处理：

- **新增**：用户新增且希望跨机器恢复的 Plugin → 确认 Plugin ID 与 marketplace/source，更新 `plugins.json`。
- **修改**：marketplace / source 等描述变化 → 更新对应条目。
- **删除 / 停用**：`plugins.json` 中存在、但主力机已不存在或被用户主动停用 → 判断是否为用户有意取消
  跨机器恢复；确认是则从 `plugins.json` 移除，未确认前保留。仅移除 manifest 不会自动清理其他机器上
  的本地插件；是否清理当前机器由用户明确要求后再执行。

- **启用状态双清单一致性**：`plugins.json` 与 `settings.portable.json` 的 `enabledPlugins` 必须同步维护。
  新增 / 移除插件时，同步在 `settings.portable.json` 的 `enabledPlugins` 加 / 删对应条目（启用写 `true`）。
  原因：Provider 切换时 cc-switch wrapper 只从 `settings.portable.json` 恢复 `enabledPlugins`；
  只更新 `plugins.json` 能保证安装，但无法保证切换后启用状态被恢复。

- 不同步插件缓存；不固定本机缓存路径；默认不锁定具体版本（除非未来明确采用版本锁定策略）。

### MCP

用官方 `claude mcp list` / `claude mcp get` 检查当前 MCP，与 `mcp.portable.json` 比较：

- **新增**：新 MCP → 判断是否应跨机器迁移；只记录便携的 server 定义。
- **修改**：便携定义（command / args / scope 等）变化 → 更新对应条目。
- **删除 / 停用**：`mcp.portable.json` 中存在、但对应 MCP 已被用户主动移除 → 判断是否应从 manifest 移除；
  确认后删除对应 portable 定义；不删除无关本机 MCP。

- 不复制完整 `.claude.json`；不保存 token / API Key / OAuth / headers / secret env；不保存机器绝对路径。
- 依赖本机 CLI 的 MCP → 只记录便携 command/args，由新机器按 `AGENT_SETUP.md` 补依赖。
- 更新 `mcp.portable.json` 后使用现有 `install.sh` / `install-mcp.py` 验证。

### Hooks

- 比较当前需要保留的 portable hooks 与 `hooks.portable.json`，按新增 / 修改 / 删除处理：
  - 只把真正应该跨机器存在的 hook 写入 `hooks.portable.json`；
  - 本机已移除且确认不再需要的 portable hook → 从 manifest 移除；
  - 临时 / 机器专属 hook 不混入。
- **portable / profile / local 分类**：
  - 纯通用命令（如 `serena-hooks`）→ `hooks.portable.json`；
  - 需要按宿主选择、但应跨同类机器恢复的能力（如 peon-ping）→ 仓库 profile，由 profile 管理器生成 hooks；
  - 临时、含凭证或真正只属于当前机器的命令 → live settings，由 wrapper 快照保护。
- profile 不得固化用户名或机器绝对路径；不要把生成后的 profile hooks 反向复制进
  `hooks.portable.json`，应更新 profile manifest/管理器并测试各系统选择逻辑。
- 不复制完整 `settings.json`。

### Settings

- `settings.portable.json` 继续作为 portable Claude 行为设置的唯一真源；不从 live `settings.json` 全量导入。
- 用户希望跨机器保持的设置，按新增 / 修改 / 删除处理：
  - 判断是否属于 portable、是否属 CC-Switch Provider / API / model 管理范围；
  - 只有符合 portable whitelist 的才更新 `settings.portable.json`；
  - 本机已移除且确认不再需要跨机器保持的 portable 设置 → 从 manifest 移除。
- **白名单外设置**：遇到用户明确想跨机器保持、但不在 `merge-settings.py` 白名单
  （`ALLOWED_TOP_LEVEL` / `ALLOWED_ENV`）内的设置时：
  - 清晰、无机器信息、无 provider / 凭证属性的 → 可考虑**保守扩充白名单**；
  - 含 Provider / API / Base URL / model / route / token 等 → 拒绝，归 CC-Switch；
  - 拿不准时向用户确认，不要静默跳过；
  - 若新增的是 top-level 键，同步检查 `command_extract` 是否也要纳入该键。
- Provider / API / Base URL / token / model routing 等继续归 CC-Switch，不进入 portable manifest。

### 环境相关组件

- 新增内容属于宿主环境能力，而非普通 Plugin / MCP / Skill 的 → 按 `AGENT_SETUP.md` 的
  “环境相关组件”抽象处理。
- peon-ping 的权威声明位于 `profiles/peon-ping/profile.json`；同步时只更新固定上游 revision、
  archive 哈希、profile 选择规则和管理逻辑，不导入 live runtime、packs、配置、状态或日志。
- profile/adapter 不固化机器用户名、绝对路径或凭证；更新后至少覆盖 detect、隔离安装、
  legacy-hook 迁移、幂等 reconcile 和 doctor/status 验证。
- 删除 / 停用同样按意图判断：profile 不再需要时移除对应条目，不残留占位。

## 三、安全边界

同步过程中继续禁止：

- 完整复制 `.claude.json`
- 完整复制 `settings.json`
- 复制 CC-Switch DB
- 同步 sessions / history / projects / cache
- 同步插件缓存
- 同步 API Key / token / OAuth
- 读取后回显真实敏感凭证
- 使用 `env` / `printenv` 调试敏感环境

删除同步也不是 destructive sync：

- 不自动删除无关的本机 Plugin / MCP / Skill；
- 仓库 manifest 移除首先表示"停止跨机器恢复"；
- 是否同时清理当前机器，由用户明确要求再执行。

## 四、验证

完成修改后：

1. 运行 portable safety audit；
2. 检查 `git diff`；
3. 运行 `./install.sh`；
4. 运行 `./doctor.sh`；
5. 验证 Plugin / MCP / hooks 等对应状态；
6. 确认没有 Provider / API / 凭证 / 机器绝对路径进入 Git。

若 doctor 因用户明确不需要的可选依赖（例如 MinerU）返回非 0，不应简单判定同步失败。

## 五、提交

修改和验证完成后：

- 向用户展示准备提交的文件；
- 确认没有异常内容；
- commit；
- push。

不提交本机备份或临时文件。

## 六、与现有工具的关系

不替换、不重构 `update.sh`。职责保持：

- `update.sh` → 帮助发现 / 导入已有白名单文件变化；profile-owned peon skills/runtime 不从 live 目录反向导入。
- `AGENT_SYNC.md` → 告诉 Agent 如何理解这些变化、处理 Plugin / MCP / settings / hooks 等 manifest，
  并完成验证和 Git 同步。
- `AGENT_SETUP.md` → 新机器根据 Git 目标状态恢复环境。

最终形成：

```text
主力机器 → AGENT_SYNC.md → Git → AGENT_SETUP.md → 新机器
```

### update.sh 的非交互行为

现有 `update.sh` 的导入确认依赖交互终端。当 Agent 当前执行环境无法交互时：

- 可以使用 `update.sh` 发现变化和查看 diff；
- 不要因为 `update.sh` 没有执行导入就判断"没有变化"；
- Agent 可以按照本协议的安全规则自行更新对应仓库文件 / manifest；
- 更新后仍然运行现有 audit / install / doctor 进行验证。

不因此修改或重构 `update.sh`。
