# `/init` 命令 — 设计文档

**日期**: 2026-06-15
**状态**: 已批准（待实现）
**方案**: A（极简 LLM 驱动）
**作者**: brainstorming session 产出

---

## 1. 目标

为 Mini-Agent 添加 `/init` 斜杠命令，在工作区根目录自动生成 `AGENTS.md`，让 Agent 在后续会话中获得项目级上下文（构建命令、架构、约定等）。功能对标 Claude Code 的 `/init`。

**非目标**：
- 不生成全局配置（`~/.mini-agent/AGENTS.md`）
- 不修改 Mini-Agent 自身的用户配置流程
- 不做交互式向导

## 2. 背景

### 2.1 业界参考

调研 Claude Code 的 `/init` 实现：

| 维度 | Claude Code 做法 |
|------|----------------|
| 执行架构 | 直接在主对话流执行（非 subagent） |
| 扫描策略 | Glob 项目结构 + 优先读 `README.md` / `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod` + `.cursorrules` / `.github/copilot-instructions.md` + `git log/status` |
| 输出位置 | `./CLAUDE.md`（项目根） |
| 冲突处理 | 直接覆盖（不合并） |
| 输出长度 | 官方建议 60-300 行 |
| 输出原则 | **只写 Claude 无法从代码推断的内容**；不写 API 文档、不写语言标准约定、不写频繁变化的信息 |

### 2.2 选定方案

**方案 A：极简 LLM 驱动** — 在命令分发处加 `/init` 分支，构造一段提示词作为普通 user message 注入 agent，让 Agent 用现有工具（Read/Bash/Glob/Grep + Write）完成扫描与写入。

**为何不选其他方案**：
- B（模板填充）：模板僵化、对非典型项目适配差
- C（Python 全分析）：与"LLM 驱动"目标冲突，要维护检测规则
- D（混合预激）：YAGNI，A 验证后再演进
- E（作为 Skill）：Skill 是 LLM 调用的工具，不是用户输入的斜杠命令，架构不匹配
- F（两阶段 pipeline）：Mini Agent 的 Agent 类未设计为可链式调用

### 2.3 与 Mini-Agent 哲学契合

CLAUDE.md 第一句："Mini Agent 是一个最小化 AI Agent 演示项目"。`/init` 实现应保持极简，避免引入新执行模型或新依赖。

## 3. 关键决策

| # | 决策点 | 选定方案 | 理由 |
|---|--------|---------|------|
| 1 | 生成方式 | LLM 驱动，复用 Agent + 现有工具链 | Claude Code 验证过；最小化代码 |
| 2 | 入口形式 | 仅 REPL 斜杠命令 `/init` | 与 `/help` `/clear` 等一致 |
| 3 | 冲突处理 | 不检查、直接覆写 | 与 Claude Code 一致；用户自己负责 |
| 4 | 输出路径 | `<workspace>/AGENTS.md` | 与 commit 841e99c 的 AGENTS.md 标准对齐 |
| 5 | 输出语言 | LLM 自动推断（按 README/代码注释语言） | 业界做法；无硬编码偏好 |
| 6 | 输出结构 | 提示词软性要求标准章节方向 | 兼顾结构化与灵活性，避免模板僵化 |
| 7 | 输出长度 | 硬约束 60-300 行 | 借鉴 Claude Code 实践 |
| 8 | 提示词位置 | 独立模块 `mini_agent/commands/init_command.py` | 防止 cli.py 膨胀（已 999 行） |
| 9 | 失败行为 | 沿用 Agent 的异常处理（Esc 可取消） | 复用现有取消机制 |
| 10 | Token 管理 | 沿用 Agent 的 `token_limit` 摘要机制 | 大项目扫描可能超限，依赖现有降级 |

## 4. 架构

### 4.1 数据流

```
用户输入 /init
  │
  ▼
cli.py 命令分发
  │  elif command == "/init":
  │      user_input = INIT_PROMPT_TEMPLATE.format(workspace=workspace_dir)
  │      # 不 continue，落入下方"普通对话"分支
  │
  ▼
cli.py 主循环既有 agent 执行流程
  │  • agent.add_user_message(user_input)
  │  • 启动 Esc 监听线程
  │  • agent_task = asyncio.create_task(agent.run())
  │  • 轮询 confirmation_request
  │
  ▼
Agent 自主执行（用户可见工具调用过程）
  │  • Read README.md
  │  • Bash: ls / git log / git status
  │  • Read pyproject.toml / package.json / ...
  │  • Read .cursorrules / .github/copilot-instructions.md（若存在）
  │  • Read 关键源码文件（按需）
  │  • Write AGENTS.md
  │
  ▼
完成，Agent 在回复中报告 "Wrote AGENTS.md (xxx lines)"
```

### 4.2 不引入的组件

- ❌ 新的 Tool 子类
- ❌ 新的 LLM 客户端调用
- ❌ 新的配置项（config.yaml 不动）
- ❌ Python 端的项目分析逻辑
- ❌ 预览/确认 UI

## 5. 提示词设计（核心工件）

提示词是本特性的唯一"业务逻辑"。要点：

### 5.1 必须包含的指令

1. **任务定义**：扫描当前工作区，生成 `AGENTS.md` 给未来的 Agent 会话提供项目上下文。
2. **扫描建议**（非强制顺序）：
   - 先用 Bash 跑 `ls -la` 看顶层结构
   - 读 `README.md`（若存在）
   - 读项目清单文件：`pyproject.toml` / `package.json` / `Cargo.toml` / `go.mod` / `pom.xml` / `build.gradle`（按存在的读）
   - 读其他 AI 工具规则：`.cursorrules` / `.github/copilot-instructions.md` / `AGENTS.md`（若已存在，作为参考但不照抄）
   - 跑 `git log --oneline -10` 和 `git status` 了解项目状态
   - 按需读关键源码目录的入口文件（如 `src/main.py`、`lib/index.js`）
3. **输出路径**：写入 `<workspace>/AGENTS.md`（用 Write 工具）。
4. **输出语言**：与 `README.md` 主要语言一致；若 README 不存在，与代码注释语言一致。
5. **必须包含的章节**（顺序可调）：
   - 项目概述（1-3 句话说明项目是什么）
   - 开发命令（构建、测试、运行、lint）
   - 架构（核心组件、目录组织）
   - 关键约定（代码风格、命名规范、提交规范）
   - 测试策略（如何跑测试、测试组织）
6. **硬约束**：
   - 总长度 60-300 行
   - **不写**：API 文档、语言标准约定、明显能从代码推断的内容、频繁变化的信息（如版本号）
   - **只写**：未来 Agent 无法从代码直接推断的内容
7. **完成报告**：写完文件后，简短报告路径和行数。

### 5.2 提示词模板（示意）

实际提示词存放在 `mini_agent/commands/init_command.py` 的 `INIT_PROMPT_TEMPLATE` 常量。模板包含 `{workspace}` 占位符（运行时替换）。

```python
INIT_PROMPT_TEMPLATE = """\
Please analyze the current workspace ({workspace}) and generate an AGENTS.md \
file at the workspace root to give future Agent sessions project-specific context.

Suggested exploration steps (adapt as needed):
1. Run `ls -la` to see the top-level structure.
2. Read README.md if it exists.
3. Read project manifest files: pyproject.toml / package.json / Cargo.toml / \
go.mod / pom.xml / build.gradle (whichever exists).
4. Read other AI assistant rule files if they exist: .cursorrules, \
.github/copilot-instructions.md, existing AGENTS.md (as reference only, do not copy).
5. Run `git log --oneline -10` and `git status` for project context.
6. Read entry-point source files as needed (e.g., src/main.py, lib/index.js).

Then use the Write tool to create {workspace}/AGENTS.md.

Output requirements:
- Language: match the primary language of README.md, or code comments if no README.
- Length: 60-300 lines total.
- Include these sections (order may vary):
  - Project Overview (1-3 sentences)
  - Development Commands (build, test, run, lint)
  - Architecture (core components, directory layout)
  - Key Conventions (code style, naming, commit rules)
  - Testing Strategy (how to run tests, test organization)
- DO NOT include: API documentation, standard language conventions, \
information easily inferred from code, frequently-changing info (e.g., version numbers).
- ONLY include: things a future Agent cannot infer directly from the code.

After writing, briefly report the file path and line count.
"""
```

## 6. 详细变更清单

### 6.1 新增（2 个文件）

| 文件 | 内容 |
|------|------|
| `mini_agent/commands/__init__.py` | 空文件（创建包） |
| `mini_agent/commands/init_command.py` | `INIT_PROMPT_TEMPLATE` 常量（仅此一项，无函数封装） |

### 6.2 修改 — `mini_agent/cli.py`

| 改动 | 位置 |
|------|------|
| 新增 import：`from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE` | 文件顶部 import 区（约 30-42 行附近） |
| 新增命令分支：`elif command == "/init":` 内对 `user_input` 赋值 init prompt 后 `break`/`continue` 走既有 agent 执行流程（详见 §7） | 命令分发 if/elif 链（约 813-820 行 `/bg` 分支后） |
| 在 `print_help()` 输出中加 `/init` 行 | `print_help()` 函数（约 197 行 Available Commands 区） |
| 在 `command_completer` 词表中加 `"/init"` | `WordCompleter([...])`（约 709 行） |

### 6.3 不修改

- ❌ `mini_agent/config/config.yaml` — 不加新配置项
- ❌ `mini_agent/config/system_prompt.md` — `/init` 用 user message 注入，不动 system prompt
- ❌ `mini_agent/agent.py` — Agent 类不变
- ❌ `mini_agent/tools/*` — 工具集不变

## 7. 实现策略：复用主循环（关键简化）

`/init` 不单独封装执行函数，而是把 init prompt 当成"特殊的用户输入"，让 cli.py 主循环既有的 agent 执行流程接管。

### 7.1 设计动机

cli.py 主循环里"普通对话"分支已经有完整的：
- Esc 监听线程（取消支持）
- `agent.confirmation_request` 轮询（ConfirmSelector 集成）
- `agent_task = asyncio.create_task(agent.run())`
- 异常 try/except
- 后台进程状态打印
- `print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")` 分隔

如果 `/init` 自己跑 agent，需要复制上面这一坨。所以让 `/init` 走"伪装成普通输入"的路。

### 7.2 cli.py 改动示意

```python
# 命令分发 if/elif 链里：
elif command == "/init":
    user_input = INIT_PROMPT_TEMPLATE.format(workspace=workspace_dir)
    # 不用 continue，让代码落入下方"Normal conversation"分支
    # 但要跳过 "if user_input.lower() in ['exit', 'quit', 'q']" 检查
    # 方法：用标志位或重构分支判断
```

具体跳转机制（二选一，实现时选其一）：

**方案 (a) — `continue_flag` 标志位**：
```python
elif command == "/init":
    user_input = INIT_PROMPT_TEMPLATE.format(workspace=workspace_dir)
    is_init = True
    # break 出命令分支
else:
    is_init = False
# ... 后续代码：if not is_init and user_input.lower() in ['exit', ...]: break
```

**方案 (b) — 直接在 `/init` 分支调 agent**：
若方案 (a) 让主循环控制流变得不直观，退回到在 `/init` 分支直接调用 agent.run()，复制一份 Esc 监听逻辑（约 50 行重复代码）。

**实现优先级**：先试方案 (a)，主循环读起来还清晰就用它；否则降级到方案 (b)。writing-plans 阶段决定。

### 7.3 为何不封装 `handle_init_command`

最初设计想封装 `async def handle_init_command(agent, workspace_dir)`，但发现：
- 函数内部就是"构造 prompt + 跑 agent"，逻辑太薄
- 跑 agent 需要的 Esc/confirmation/异常处理都在主循环里，封装后要么复制要么传一堆依赖
- 把 prompt 当 user_input 注入更符合 Agent 的执行模型

所以最终 init_command.py 只存常量，不存函数。

## 8. 边缘情况

| 情况 | 行为 |
|------|------|
| 工作区已有 `AGENTS.md` | 不检查，Agent 会读到并作为参考（提示词中说明）；最终被 Write 覆盖 |
| 工作区为空目录 | Agent 探索后写一个最小化的 AGENTS.md（可能只含概述和占位章节） |
| 无 `README.md` | Agent 按代码注释语言推断输出语言 |
| 不是 git 仓库 | `git log/status` 会失败，Agent 自行跳过（不影响主流程） |
| API key 未配置 | Agent.run() 会报错，沿用现有错误打印 |
| 大项目扫描超 token_limit | 触发现有摘要机制；可能影响输出质量，可接受 |
| Agent 中途被 Esc 取消 | 文件可能未写出或部分写出，沿用现有取消提示，不特殊处理 |

## 9. 范围外（明确不做）

| 项目 | 推迟到 | 理由 |
|------|--------|------|
| Python 预扫描上下文预激（方案 D） | 后续演进 | YAGNI；先用 A 验证 token 消耗是否可接受 |
| 生成预览/确认 UI | 不做 | 用户明确选"不检查" |
| `mini-agent init` CLI 子命令 | 不做 | 用户明确选"仅斜杠命令" |
| 多语言项目（monorepo）专门处理 | 不做 | Agent 自己判断，不预设规则 |
| 增量更新（已存在 AGENTS.md 时智能合并） | 不做 | 直接覆盖，与 Claude Code 一致 |
| 内置 Skill 化（作为 `get_skill` 工具） | 不做 | 架构不匹配（Skill 是 LLM 调用，不是用户输入） |

## 10. 验收标准

- [ ] `pytest tests/ -v` 全部通过（无新增测试，回归保护）
- [ ] 启动 `mini-agent`，输入 `/init`，Agent 开始扫描工作区
- [ ] 扫描过程在终端可见（Read/Bash 工具调用日志）
- [ ] 完成后 `<workspace>/AGENTS.md` 存在，行数在 60-300 范围
- [ ] `/help` 输出包含 `/init` 行
- [ ] Tab 补全包含 `/init`
- [ ] Esc 可中途取消 `/init` 执行
- [ ] 在 Mini-Agent 自己的仓库跑 `/init`，输出与现有 `CLAUDE.md` 在结构和质量上可比
- [ ] 在一个简单的 Python 项目（只有 `pyproject.toml` 和 `src/`）跑 `/init`，输出包含构建/测试命令

## 11. 风险与回滚

- **风险等级**：极低（仅新增斜杠命令分支 + 一个提示词常量，无核心逻辑改动）
- **风险点**：
  - 提示词质量不达标，输出 AGENTS.md 信息密度低 → 实现后实测迭代
  - 复用主循环跳转结构的实现方式可能让 cli.py 控制流不直观 → 实现时若不优雅退回到独立封装
- **回滚**：删除 `mini_agent/commands/` 目录 + 还原 cli.py 的 4 处改动即可

## 12. 实现顺序建议（供 writing-plans 参考）

1. **创建包结构**：`mini_agent/commands/__init__.py` + `mini_agent/commands/init_command.py`
2. **写提示词常量**：`INIT_PROMPT_TEMPLATE`（按 §5.2）
3. **接入 cli.py**：import + 命令分支（优先用 §7 的"复用主循环"方案）
4. **更新 `print_help()` 和 `command_completer`**：加 `/init` 行
5. **运行测试**：`pytest tests/ -v` 全绿
6. **手动验证**：在 Mini-Agent 仓库自身和一个简单的 Python 测试项目各跑一次 `/init`，检查输出质量
7. **迭代提示词**：根据实测结果调整 §5.2 模板（可能需要更明确的章节指引或扫描顺序提示）
