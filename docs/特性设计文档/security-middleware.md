# 安全中间件（Security Middleware）

## 概述

为 Mini-Agent 添加可插拔的安全中间件，对 Shell 命令和文件操作进行风险评估和拦截。核心能力包括：命令三级分类（DENY/CONFIRM/SAFE）、复合命令拆分解析、交互式用户确认、规则持久化、环境变量脱敏。

## 为什么需要这个模块

Mini-Agent 作为 AI Agent 可以自主执行 Shell 命令和文件操作，但没有安全机制意味着：

1. **误操作风险** — LLM 可能生成 `rm -rf /` 等破坏性命令并直接执行
2. **敏感信息泄露** — 环境变量中的 API Key、Token 可能被子进程读取
3. **不可控行为** — 用户无法审查和干预 Agent 的危险操作

参考了 Claude Code、OpenAI Codex 等产品的安全设计，选择了可插拔架构方案，在保持灵活性的同时提供基础防护。

## 架构设计

```
                    ┌─────────────┐
                    │   CLI 入口   │
                    │  (cli.py)   │
                    └──────┬──────┘
                           │ 创建 SecurityMiddleware
                    ┌──────▼──────┐
                    │  Middleware  │  编排层：分类 → 确认 → 决策
                    └──┬───┬───┬──┘
              ┌────────┘   │   └────────┐
       ┌──────▼──────┐    │     ┌──────▼──────┐
       │  Classifier  │    │     │   Confirm   │
       │  (命令分类)   │    │     │  (用户确认)  │
       └─────────────┘    │     └─────────────┘
                          │
              ┌───────────┼───────────┐
       ┌──────▼──────┐ ┌──▼───┐ ┌────▼────┐
       │  BashTool   │ │Write │ │  Edit   │
       │  (命令检查)  │ │Tool  │ │  Tool   │
       └─────────────┘ └──────┘ └─────────┘
```

### 设计理由

**为什么是可插拔架构而非硬编码检查：**
- 工具通过 `security: SecurityMiddleware | None = None` 接入，`None` 时完全跳过安全检查
- 未来可以替换为 OS 级沙箱（macOS Seatbelt / Linux bubblewrap）而无需改动工具代码
- 不影响已有功能，向后兼容

**为什么用三级分类而非简单的黑白名单：**
- DENY（无条件阻止）— 处理 `rm -rf /`、`mkfs` 等不可逆操作
- CONFIRM（需用户确认）— 处理 `sudo`、`apt install` 等有上下文依赖的操作
- SAFE（自动放行）— 处理 `git status`、`ls` 等只读操作
- 黑白名单只有两级，无法处理"取决于用户意图"的灰色地带

**为什么自己写复合命令解析而非用 shlex：**
- `shlex` 不理解 `|`、`&&`、`||`、`;` 这些 shell 操作符
- 需要对每个子命令独立分类，取最高风险级别
- 状态机方式逐字符解析，正确处理引号嵌套和 `$()` 子 shell

## 模块结构

### `mini_agent/security/` 目录

| 文件 | 职责 |
|------|------|
| `models.py` | `RiskLevel` 枚举（SAFE=0, CONFIRM=1, DENY=2）、`SecurityDecision` 数据类 |
| `classifier.py` | 命令分类器，内置正则规则 + 复合命令拆分 + 用户自定义规则 |
| `confirm.py` | 交互式用户确认（y/n/a），规则持久化到 `~/.mini-agent/security_rules.json` |
| `middleware.py` | 安全中间件，编排分类 → 确认 → 环境脱敏 → 路径保护 |
| `__init__.py` | 包导出，带 try/except 防御性导入 |

### `mini_agent/config.py` 扩展

```python
class SecurityConfig(BaseModel):
    mode: str = "workspace-write"          # full-access / workspace-write / read-only
    non_interactive_fallback: str = "deny" # 非交互模式下 CONFIRM 命令的处理策略
    rules_file: str = "~/.mini-agent/security_rules.json"
    extra_allow: list[str] = []            # 用户自定义允许规则（正则）
    extra_deny: list[str] = []             # 用户自定义拒绝规则
    extra_confirm: list[str] = []          # 用户自定义确认规则
```

### 工具集成方式

所有工具通过可选参数接入，不影响已有代码：

```python
class BashTool(Tool):
    def __init__(self, workspace_dir=None, security=None):
        self.security = security  # None 时跳过安全检查

    async def execute(self, command, ...):
        if self.security:
            decision = await self.security.check_command(command)
            if not decision.allowed:
                return ToolResult(success=False, error=f"被安全策略拒绝: {decision.reason}")
        # 正常执行...
```

## 核心机制

### 1. 命令分类（classifier.py）

**内置规则优先级：** 用户自定义 DENY > 用户自定义 ALLOW > 内置 DENY > 内置 ALLOW > 内置 CONFIRM > 模式默认

**复合命令拆分：** 逐字符状态机，追踪引号状态和 `$()` 嵌套深度，在 `|`、`||`、`&&`、`;` 处拆分子命令，每个子命令独立分类后取最高风险级别。

```python
# 例: "cat file | grep error && rm -rf /tmp/test; echo done"
# 拆分为: ["cat file", "grep error", "rm -rf /tmp/test", "echo done"]
# 风险: [SAFE, SAFE, DENY(match rm -rf), SAFE] → 最终 DENY
```

### 2. 用户确认（confirm.py）

交互模式下，CONFIRM 级别的命令会暂停执行并提示用户：

```
⚠️  危险命令检测: sudo apt install python3
原因: 需要超级用户权限
[y] 执行一次  [n] 拒绝  [a] 始终允许此类命令 >
```

选择 `[a]` 会将命令模式持久化到 JSON 文件，后续匹配的命令自动放行。

### 3. 环境变量脱敏（middleware.py）

子进程启动时使用过滤后的环境变量，移除包含以下关键词的变量：
`API_KEY`、`SECRET`、`TOKEN`、`PASSWORD`、`CREDENTIAL`、`PRIVATE_KEY`

在 `__init__` 时一次性计算并缓存，避免重复过滤。

### 4. 文件操作保护（middleware.py）

- **workspace-write 模式：** 写操作限制在工作区内
- **read-only 模式：** 禁止所有写操作
- **受保护路径：** `/etc/passwd`、`/.ssh/`、`/.env`、`/.aws/` 等，任何模式都禁止写入

### 5. 非交互模式处理

在 `task` 模式（非交互）下，遇到 CONFIRM 级别命令时：
- `non_interactive_fallback: "deny"` → 自动拒绝（默认）
- `non_interactive_fallback: "allow"` → 自动放行

## 配置方式

在 `config.yaml` 中添加 `security` 段：

```yaml
security:
  mode: workspace-write          # full-access / workspace-write / read-only
  non_interactive_fallback: deny # deny / allow
  rules_file: ~/.mini-agent/security_rules.json
  extra_allow:
    - "^my-custom-tool"
  extra_deny:
    - "dangerous-script\\.sh"
  extra_confirm:
    - "^pip install"
```

不配置时使用默认值，安全模块自动启用。

## 测试覆盖

48 个安全专项测试，覆盖以下场景：

| 模块 | 测试数 | 覆盖内容 |
|------|--------|----------|
| models | 4 | RiskLevel 排序、SecurityDecision 各状态 |
| classifier | 23 | 安全命令、拒绝命令、确认命令、复合命令拆分、三种模式、用户规则覆盖 |
| confirm | 8 | 规则加载/保存/追加、异常文件处理、枚举值 |
| middleware | 13 | 命令检查(5)、文件操作检查(6)、环境脱敏(2) |

全量测试 186 passed, 0 failed。

## 当前限制与后续完善方向

### P1 — 近期可做

1. **OS 级沙箱集成**
   - macOS: 使用 Seatbelt（`sandbox-exec`）限制文件系统和网络访问
   - Linux: 使用 bubblewrap（`bwrap`）创建命名空间隔离
   - 实现 `SandboxBackend` 抽象接口，与当前 `SecurityMiddleware` 并行

2. **命令分类增强**
   - 当前基于正则匹配，可能误判（如注释中的危险命令）
   - 可引入 AST 级别的 shell 解析（通过 `shfmt` 或类似工具）
   - 支持通配符展开后的路径检查

3. **审计日志**
   - 记录所有被拦截的命令和用户确认的操作
   - 输出到 `~/.mini-agent/audit.log`，支持 JSON 格式

### P2 — 中期改进

4. **细粒度路径控制**
   - 当前 workspace 边界检查基于路径前缀匹配
   - 可支持 `.mini-agentignore` 文件，类似 `.gitignore` 模式
   - 区分读路径白名单和写路径白名单

5. **规则管理 CLI**
   - `mini-agent security list` — 列出当前规则
   - `mini-agent security allow <pattern>` — 添加允许规则
   - `mini-agent security deny <pattern>` — 添加拒绝规则
   - `mini-agent security reset` — 清除所有用户规则

6. **速率限制**
   - 限制单次会话中的命令执行频率
   - 防止 LLM 陷入循环时反复执行相同命令

### P3 — 长期方向

7. **能力降级（Capability Reduction）**
   - 根据任务风险等级动态调整可用工具集
   - 例如：简单查询只保留只读工具，复杂任务才开放写权限

8. **沙箱快照与回滚**
   - 执行前对工作区做快照（基于 git 或 filesystem snapshot）
   - 操作失败或用户不满意时可回滚

## 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mini_agent/security/__init__.py` | 新增 | 包导出 |
| `mini_agent/security/models.py` | 新增 | RiskLevel、SecurityDecision |
| `mini_agent/security/classifier.py` | 新增 | 命令分类器 |
| `mini_agent/security/confirm.py` | 新增 | 用户确认 + 规则持久化 |
| `mini_agent/security/middleware.py` | 新增 | 安全中间件 |
| `mini_agent/config.py` | 修改 | 添加 SecurityConfig |
| `mini_agent/cli.py` | 修改 | 接入 SecurityMiddleware |
| `mini_agent/tools/bash_tool.py` | 修改 | 命令安全检查 |
| `mini_agent/tools/file_tools.py` | 修改 | 文件操作安全检查 |
| `mini_agent/acp/__init__.py` | 修复 | NewSessionRequest 必填字段 |
| `tests/test_security_*.py` | 新增 | 48 个安全测试 |
| `tests/test_acp.py` | 修复 | 无效 session 测试断言 |
