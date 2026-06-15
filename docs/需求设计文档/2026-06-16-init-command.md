# `/init` 斜杠命令 — 特性设计文档

**日期**: 2026-06-16
**分支**: `fsw`
**提交**: `0c6f944`（包含 spec、plan、代码、测试共 6 文件）
**变更规模**: +954 行，-1 行（含文档 877 行，代码 77 行）

---

## 1. 背景与目标

### 1.1 要解决的问题

Mini-Agent 通过 `~/.mini-agent/AGENTS.md`（全局）和 `<workspace>/AGENTS.md`（项目级）注入 system prompt 的机制已经存在（见 [2026-06-14 记忆系统清理](./2026-06-14-memory-system-cleanup.md)），但用户在新项目里使用 Mini-Agent 时面临两个痛点：

1. **冷启动成本高** — 项目级 `AGENTS.md` 不存在时，Agent 完全没有项目上下文。需要用户手工写一份，门槛较高。
2. **手动写作质量参差** — 即便用户愿意写，也很难把"未来 Agent 无法从代码推断的信息"提炼出来（往往会复述代码、写 API 文档、写语言标准），导致 token 浪费。

### 1.2 业界参考

对标 **Claude Code 的 `/init` 命令**，其核心做法：

| 维度 | Claude Code 做法 |
|------|----------------|
| 执行架构 | 直接在主对话流执行（非 subagent） |
| 扫描策略 | Glob + 优先读 `README.md` / 清单文件 / `.cursorrules` / `git log` |
| 输出位置 | `./CLAUDE.md`（项目根） |
| 冲突处理 | 直接覆盖（不合并） |
| 输出长度 | 官方建议 60-300 行 |
| 输出原则 | **只写 Agent 无法从代码推断的内容** |

GitHub Copilot、Cursor、aider 等同类工具都有类似机制（`.github/copilot-instructions.md`、`.cursorrules`、`conventions.md`），但实现细节不如 Claude Code 公开。

### 1.3 本特性的目标

给 Mini-Agent 加一个 `/init` 斜杠命令，让 Agent 自己扫描当前工作区并写出一份合格的 `AGENTS.md`。**非目标**：

- ❌ 不做交互式向导
- ❌ 不做生成预览/确认 UI
- ❌ 不做 `mini-agent init` CLI 子命令（仅 REPL 斜杠命令）
- ❌ 不做旧文件合并/迁移（直接覆盖）

---

## 2. 关键设计决策

| # | 决策点 | 选定方案 | 否决的替代 | 理由 |
|---|--------|---------|----------|------|
| 1 | 生成方式 | **LLM 驱动**，复用 Agent + 工具链 | Python 静态扫描 / 模板填充 / 混合预激 | 与 Claude Code 一致；Mini Agent 已有 Read/Bash/Write 工具，让 Agent 自己探索即可 |
| 2 | 入口形式 | **仅 REPL 斜杠命令** | CLI 子命令 | 与 `/help` `/clear` 等保持一致；CI 场景需求弱 |
| 3 | 冲突处理 | **不检查，直接覆写** | 询问用户 / 自动备份 | 与 Claude Code 一致；备份责任交给用户（任务 4 验收步骤里有提示） |
| 4 | 输出路径 | `<workspace>/AGENTS.md` | `~/.mini-agent/AGENTS.md` | 项目级规则文件，可 commit 到 git 共享 |
| 5 | 输出语言 | **LLM 自动推断**（按 README/注释语言） | 固定中文 / 固定英文 | 业界做法；不预设偏好 |
| 6 | 输出结构 | **提示词软性要求 5 个标准章节方向** | 模板硬填充 / 完全自由发挥 | 兼顾结构化与灵活性 |
| 7 | 输出长度 | **硬约束 60-300 行**（写进提示词） | 软性"keep concise" | 数字约束更有效（Claude Code 实践） |
| 8 | 提示词位置 | **独立模块** `mini_agent/commands/init_command.py` | 内联 cli.py | cli.py 已 999 行，避免膨胀 |
| 9 | 失败/取消 | **沿用 Agent 现有 Esc 取消 + 异常处理** | 单独实现取消逻辑 | 复用即免费 |
| 10 | Token 管理 | **沿用 Agent 现有 token_limit 摘要机制** | 单独预算控制 | 大项目可能超限，但代价低于自建机制 |

### 2.1 否决过的"看似更好"的方案

设计阶段评估过 7 个方案，最终选 A：

| 方案 | 评估 | 结论 |
|------|------|------|
| **A** 极简提示注入（选用） | ~50 行代码，复用所有现有机制 | ✅ |
| B 模板填充 | 模板僵化，对非典型项目适配差 | ❌ |
| C Python 全分析 | 要维护检测规则，与"LLM 驱动"冲突 | ❌ |
| D 混合预激（Python 收集事实 + LLM 总结） | 80 行换 LLM 几次工具调用 | 备选，A 验证后演进 |
| E 作为 Skill | 架构不匹配（Skill 是 LLM 调用，不是用户输入） | ❌ |
| F 两阶段 pipeline | Agent 类不支持链式调用 | ❌ |
| G 流式预览 | 与"不检查"决策冲突 | ❌ |

---

## 3. 功能实现原理

### 3.1 数据流（端到端）

```
用户在 REPL 输入 /init
  │
  ▼
cli.py 主循环（line 755-963）
  │  user_input = "/init"
  │  command = user_input.lower() = "/init"
  │
  ▼
命令分发（line 777: if user_input.startswith("/")）
  │  进入 elif command == "/init": 分支（line 822-828）
  │
  ▼
【关键设计】user_input 被覆写为完整 prompt，不调用 continue
  │  user_input = INIT_PROMPT_TEMPLATE.format(workspace=str(workspace_dir))
  │
  ▼
自然落入"Normal conversation"分支（line 833+）
  │  1. exit 检查跳过（prompt 不等于 "exit"/"quit"/"q"）
  │  2. agent.add_user_message(user_input)
  │  3. 启动 Esc 监听线程
  │  4. agent_task = asyncio.create_task(agent.run())
  │  5. 轮询 agent.confirmation_request（ConfirmSelector 集成）
  │
  ▼
Agent 自主执行（用户在终端可见全部工具调用）
  │  • Bash: ls -la / git log --oneline -10 / git status
  │  • Read: README.md / pyproject.toml / package.json / .cursorrules / ...
  │  • Read: 关键源码入口（按需）
  │  • Write: AGENTS.md
  │
  ▼
Agent 输出简短报告（"Wrote /path/to/AGENTS.md (xxx lines)"）
  │
  ▼
主循环打印分隔线，等待下一个输入
```

### 3.2 "Fall-through"模式的设计精妙之处

这是本特性最值得讲的实现细节。原始 spec 设想了两种实现：

- **方案 (a)** 用 `is_init` 标志位跳过 exit 检查
- **方案 (b)** 在 `/init` 分支内直接复制 50 行 agent 执行代码

实现时发现 cli.py 主循环的现有结构天然支持第三种更简洁的做法：**只需覆写 `user_input`，不调用 `continue`**。原因：

1. 命令分发块的 `if user_input.startswith("/"):` 没有外层 `else`
2. 覆写后的 `user_input` 是一段长 prompt，绝不会命中 `"exit"/"quit"/"q"` 检查
3. 后续代码（`agent.add_user_message` / Esc 监听 / `agent.run()` / ConfirmSelector / 异常处理）全部免费复用

最终代码只有 1 行有效语句 + 5 行解释注释：

```python
elif command == "/init":
    # Inject the init prompt as a normal user message and fall
    # through to the regular agent execution flow below (which
    # provides Esc cancellation, ConfirmSelector integration,
    # background-process status printing, and error handling).
    # No `continue` here — we want the fall-through.
    user_input = INIT_PROMPT_TEMPLATE.format(workspace=str(workspace_dir))
```

**代价**：需要一段注释防止未来维护者"修 bug"误加 `continue`。已在代码里写明。

---

## 4. 代码实现

### 4.1 文件清单与职责

| 文件 | 角色 | 行数 | 职责 |
|------|------|------|------|
| `mini_agent/commands/__init__.py` | 包标记 | 5 行 | 声明 `commands` 子包，docstring 解释用途 |
| `mini_agent/commands/init_command.py` | 提示词仓库 | 46 行 | 仅含 `INIT_PROMPT_TEMPLATE` 常量，无函数 |
| `mini_agent/cli.py` | 接入点 | +11 行 / -1 行 | 4 处改动（见 4.2） |
| `tests/test_init_command.py` | 单元测试 | 27 行 | 3 个测试覆盖模板结构 |

### 4.2 cli.py 的 4 处改动

```python
# 改动 1：import（line 32，按字母序插入）
from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE

# 改动 2：命令分支（line 822-828）
elif command == "/init":
    user_input = INIT_PROMPT_TEMPLATE.format(workspace=str(workspace_dir))
    # 不 continue，落入主循环既有的 agent 执行流程

# 改动 3：/help 文本（line 202）
{Colors.BRIGHT_GREEN}/init{Colors.RESET}      - Scan project and generate AGENTS.md

# 改动 4：WordCompleter（line 714）
["/help", "/clear", "/history", "/stats", "/init", "/log", "/bg", ...]
```

### 4.3 提示词结构（核心工件）

`INIT_PROMPT_TEMPLATE` 是本特性唯一的"业务逻辑"，46 行英文 prompt，分 4 个语义块：

```python
INIT_PROMPT_TEMPLATE = """\
# 块 1：任务定义
Please analyze the current workspace ({workspace}) and generate an \
AGENTS.md file at the workspace root to give future Agent sessions \
project-specific context.

# 块 2：探索步骤（建议性，非强制）
Suggested exploration steps (adapt as needed):
1. Run `ls -la` to see the top-level structure.
2. Read README.md if it exists.
3. Read project manifest files: pyproject.toml / package.json / Cargo.toml / \
go.mod / pom.xml / build.gradle (whichever exists).
4. Read other AI assistant rule files if they exist: .cursorrules, \
.github/copilot-instructions.md, existing AGENTS.md (as reference only, \
do not copy).
5. Run `git log --oneline -10` and `git status` for project context.
6. Read entry-point source files as needed (e.g., src/main.py, lib/index.js).

# 块 3：写入指令
Then use the Write tool to create {workspace}/AGENTS.md.

# 块 4：输出约束（硬性）
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
information easily inferred from code, frequently-changing info (e.g., \
version numbers).
- ONLY include: things a future Agent cannot infer directly from the code.

# 块 5：完成报告
After writing, briefly report the file path and line count.
"""
```

**关键设计点**：
- 覆盖 **6 种语言生态**（Python/Node/Rust/Go/Java/Gradle），避免 Python 中心主义
- 明确"**作为参考但不照抄**"已存在的 `.cursorrules`/`AGENTS.md`
- "**DO NOT include**" 比 "keep concise" 这种模糊话术更有效（业界验证）
- 章节方向允许"order may vary"，给 LLM 灵活度

### 4.4 测试设计

3 个测试，覆盖模板的 **结构属性**而非行为（行为依赖 Agent + LLM，只能端到端验证）：

```python
def test_template_has_workspace_placeholder():
    """模板必须含 {workspace} 占位符"""
    assert "{workspace}" in INIT_PROMPT_TEMPLATE

def test_template_formats_without_error():
    """format() 必须成功，且 workspace 路径嵌入正确"""
    result = INIT_PROMPT_TEMPLATE.format(workspace="/tmp/fake-project")
    assert "/tmp/fake-project" in result

def test_template_mentions_required_artifacts():
    """模板必须提到关键工件（输出文件、扫描目标、长度约束）"""
    result = INIT_PROMPT_TEMPLATE.format(workspace="/tmp/x")
    assert "AGENTS.md" in result
    assert "README.md" in result
    assert "pyproject.toml" in result
    assert "60-300" in result
```

**为什么不测行为**：完整行为是"跑 /init 后 AGENTS.md 出现且内容合理"。这需要：
- 真实 LLM API 调用（耗 token、不稳定）
- 完整 Agent + 工具链初始化（CI 友好性差）
- 内容质量判断（无客观标准）

所以行为验证留给手动测试（plan 的 Task 4）。

### 4.5 不修改的部分（YAGNI）

明确 **不动**这些组件：

| 不动的东西 | 理由 |
|----------|------|
| `mini_agent/agent.py` | Agent 类不需要任何新能力 |
| `mini_agent/tools/*` | 工具集不变，Read/Bash/Write 已够用 |
| `mini_agent/config/config.yaml` | 不引入新配置项 |
| `mini_agent/config/system_prompt.md` | `/init` 用 user message 注入，不动 system prompt |
| `mini_agent/schema.py` | 不引入新的数据类型 |

整个特性净增 **77 行代码**（含注释），无任何核心架构改动。

---

## 5. 实现特点

### 5.1 极致复用

整套特性 **没有新增任何执行逻辑**。所有"运行 agent"的基础设施都来自 cli.py 主循环既有代码：

| 既有机制 | `/init` 复用方式 |
|---------|----------------|
| Esc 取消线程（line 855-895） | 直接落入 |
| `asyncio.create_task(agent.run())` | 直接落入 |
| `agent.confirmation_request` 轮询 + ConfirmSelector | 直接落入 |
| 异常 try/except | 直接落入 |
| 后台进程状态打印 | 直接落入 |
| `_quiet_cleanup()` MCP 清理 | 直接落入 |

这意味着 `/init` **天然支持**：取消、确认、错误处理、后台进程感知、MCP 清理。零成本。

### 5.2 单一职责的代码组织

`mini_agent/commands/` 子包的设立，为未来其他"复杂提示词型"斜杠命令（如 `/refactor`、`/test`、`/docs`）预留了扩展点。每个命令一个模块，cli.py 只负责分发。

### 5.3 提示词驱动而非代码驱动

本特性 **没有 if/else 分支判断"项目类型"**，没有 Python 端的"如果检测到 pyproject.toml 则..."。所有项目类型识别全部交给 LLM 用工具调用完成。好处：

- 适应任意语言生态（不需要为 Swift、Kotlin、Zig 等加规则）
- 适应非典型项目（混合栈、monorepo）
- 维护成本低（提示词迭代即可）

### 5.4 渐进式 TDD

测试先行，但只测"工件结构"。提示词的真正质量靠 **手动验收**（plan Task 4 的 10 步流程）：
- 在 Mini-Agent 自身仓库跑一遍 `/init`，对比生成的 AGENTS.md 与现有 CLAUDE.md
- 在一个最小 Python 项目跑一遍，验证基础可用性
- 验证 Esc 中途取消
- 验证 `/help` 和 Tab 补全

### 5.5 安全失败

`/init` 不会破坏现有数据：
- 写 `AGENTS.md` 之前 Agent 会读旧文件（提示词里写了 "as reference only"）
- 即使 Agent 中途被 Esc 取消，最坏情况是 AGENTS.md 没写出或部分写出
- plan Task 4 Step 4 提示用户预先 `cp AGENTS.md AGENTS.md.bak`

---

## 6. 已知问题与限制

### 6.1 提示词质量未充分验证

实现后 **没有真实跑过 `/init`** 生成 AGENTS.md。可能的问题：
- 输出长度可能超过 300 行（LLM 对硬数字约束不严格）
- 章节覆盖可能不全（"Key Conventions"容易被省略）
- 语言推断可能错误（中文项目但 README 是英文）

**缓解**：plan Task 4 设计了详细的验证步骤；如果质量不达标，迭代 `INIT_PROMPT_TEMPLATE` 即可，无需改架构。

### 6.2 Token 消耗不可控

LLM 驱动方式无法预算 token。大项目（10 万行代码）可能：
- Agent 探索时多次 Read，消耗大量 input token
- 触发 Agent 的 `token_limit` 摘要机制，影响输出质量
- 单次 `/init` 成本无法预估

**缓解**：暂无。这是方案 A 的固有代价。如果验证后发现问题严重，可演进到方案 D（Python 预扫描上下文预激）。

### 6.3 直接覆写有数据丢失风险

用户精心写的 `AGENTS.md` 会被无提示覆盖。

**缓解**：
- 提示词里要求 Agent "as reference only, do not copy" 已存在的 AGENTS.md，理论上 LLM 会读取并尊重原内容
- 但 LLM 不保证 100% 保留用户意图
- plan Task 4 Step 4 提示用户备份

**未来选项**：演进时可加一个软提示"已检测到 AGENTS.md，是否继续？(y/N)"。

### 6.4 不支持 `/init` 加参数

当前实现下 `/init extra-args`（如 `/init --force`）会命中 "Unknown command"。因为 dispatch 用 `command = user_input.lower()` 全字符串比较，和 `/help`、`/clear` 等命令的行为一致。

**是否要修**：不需要。当前没有参数需求。如果未来要加（如 `/init --global` 写到 `~/.mini-agent/AGENTS.md`），那时再改。

### 6.5 非异步/非 Python 项目的语言推断

提示词说"match the primary language of README.md, or code comments if no README"。如果项目既无 README 又无显著代码注释，语言推断会失败。

**当前行为**：LLM 自己挑一个（默认英文）。
**改进方向**：暂无。属于边缘情况。

---

## 7. 后续演进路径

### 7.1 短期（验证 + 微调）

| 项 | 触发条件 | 做法 |
|----|---------|------|
| 提示词迭代 | 实测后发现输出质量不达标 | 改 `INIT_PROMPT_TEMPLATE` 常量，无需新代码 |
| 章节强制化 | LLM 经常漏关键章节 | 在提示词里加 "MUST include all 5 sections above" |
| 长度硬约束 | 实际输出经常超 300 行 | 提示词加更强语气："STRICTLY between 60 and 300 lines" |

### 7.2 中期（可选增强）

| 项 | 价值 | 实现成本 |
|----|------|---------|
| **Python 预扫描上下文预激**（方案 D） | 节省 30-50% 探索 token | ~80 行 Python 检测函数 |
| 旧 AGENTS.md 备份 | 防止意外丢失用户工作 | 5 行：写前 `cp AGENTS.md AGENTS.md.bak.{ts}` |
| 全局版本 `/init --global` | 一键生成 `~/.mini-agent/AGENTS.md` | 改提示词 + 加参数解析（需重构 dispatch） |
| 多语言生态定制 prompt | 对 Python/JS/Rust 项目分别用更精准的提示词 | 检测 + 维护多个模板常量 |

### 7.3 长期（架构级）

| 项 | 价值 | 实现成本 |
|----|------|---------|
| **增量更新模式** `/init --update` | 已有 AGENTS.md 时智能合并，保留用户手写内容 | 高：需要 diff 策略或 LLM 二次调用 |
| **两阶段 pipeline**（Explore → Synthesize） | 输出质量更高，摘要可缓存 | 高：Mini Agent 的 Agent 类未设计为链式调用 |
| **作为内置 Skill** | LLM 可在新项目自动触发初始化 | 高：Skill 系统是 LLM 调用，不是用户输入，架构改动大 |
| `mini-agent init` CLI 子命令 | CI/CD 集成、批量化 | 低：复用 `INIT_PROMPT_TEMPLATE` + 非交互模式 |

### 7.4 演进哲学

遵循 Mini-Agent 项目"最小化演示"哲学：
- **优先迭代提示词**（成本最低）
- **其次加 Python 预扫描**（成本可控）
- **最后才动架构**（成本高，需谨慎）

每次演进都要回答两个问题：
1. 现有方案在哪个具体场景下不够用了？
2. 演进方案的复杂度增量是否与收益匹配？

---

## 8. 验收与验证

### 8.1 自动化测试（已通过）

- ✅ `pytest tests/test_init_command.py -v` — 3/3 PASS
- ✅ `pytest tests/ -v --ignore=tests/test_session_integration.py --ignore=tests/test_integration.py` — 216 passed, 3 skipped
- ✅ `python -c "from mini_agent import cli"` — 干净导入
- ✅ `python -c "from mini_agent.cli import print_help; print_help()"` — `/init` 行可见且对齐

### 8.2 待手动验证（plan Task 4，需用户执行）

未在自动化里覆盖的场景：
- ⏳ 真实跑 `/init` 看 Agent 工具调用过程
- ⏳ 验证生成的 AGENTS.md 内容质量（60-300 行、章节齐全、语言正确）
- ⏳ 验证 Esc 中途取消
- ⏳ 验证 Tab 补全 `/i` → `/init`
- ⏳ 在非 Python 项目（如纯 JS 项目）验证语言适应性

执行步骤详见 `docs/superpowers/plans/2026-06-15-init-command.md` Task 4。

### 8.3 失败回滚

如果特性上线后发现严重问题：
```bash
git revert 0c6f944
```
单次提交即可完整回滚（无依赖其他改动）。

---

## 9. 参考

- **Spec**: [`docs/superpowers/specs/2026-06-15-init-command-design.md`](../superpowers/specs/2026-06-15-init-command-design.md)
- **Plan**: [`docs/superpowers/plans/2026-06-15-init-command.md`](../superpowers/plans/2026-06-15-init-command.md)
- **业界参考**: Claude Code `/init` 命令（[官方最佳实践](https://code.claude.com/docs/en/best-practices)）
- **相关特性**: [记忆系统清理与标准化](./2026-06-14-memory-system-cleanup.md)（建立了 AGENTS.md 标准命名）

---

**文档版本**: v1.0（特性实现完成后撰写）
**实现状态**: 代码 + 测试完成，手动验收待执行
**作者**: brainstorming + subagent-driven development 协作产出
