"""Security middleware — orchestrates classification, confirmation, and env sanitization."""

import os
from pathlib import Path

from mini_agent.config import SecurityConfig
from mini_agent.security.classifier import CommandClassifier
from mini_agent.security.confirm import UserConfirmation
from mini_agent.security.models import RiskLevel, SecurityDecision


class SecurityMiddleware:
    """Central security check for BashTool commands and FileTool operations."""

    def __init__(
        self,
        security_config: SecurityConfig,
        workspace_dir: str,
        interactive: bool = True,
    ):
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
        self._clean_env = self._sanitize_env()
        # 本次会话中已确认过的命令（"执行一次" 用，不持久化）
        self._session_allowed: set[str] = set()

    async def check_command(self, command: str) -> SecurityDecision:
        """Check if a shell command is allowed to execute."""
        risk, reason = self.classifier.classify(
            command, self.mode, self.confirmation.user_rules
        )

        if risk == RiskLevel.DENY:
            return SecurityDecision(allowed=False, reason=reason)

        if risk == RiskLevel.CONFIRM:
            # 本次会话已确认过的命令直接放行
            if command in self._session_allowed:
                self._session_allowed.discard(command)
                return SecurityDecision(allowed=True, command=command, env=self._clean_env)
            if not self.interactive:
                if self.non_interactive_fallback == "deny":
                    return SecurityDecision(allowed=False, reason=f"[非交互模式] {reason}")
                # fallback="allow" → fall through to allow
            else:
                # 不再在此处调用 input()，返回 needs_confirmation 让上层处理
                return SecurityDecision(
                    allowed=False,
                    reason=reason,
                    command=command,
                    needs_confirmation=True,
                )

        return SecurityDecision(allowed=True, command=command, env=self._clean_env)

    def apply_confirmation(self, command: str, always: bool = False) -> SecurityDecision:
        """用户确认后调用，放行命令的下次执行。

        Args:
            command: 被确认的命令
            always: 如果为 True，添加永久规则；否则仅本次放行一次
        """
        if always:
            self.confirmation.add_permanent_rule(command)
        else:
            self._session_allowed.add(command)
        return SecurityDecision(allowed=True, command=command, env=self._clean_env)

    async def check_file_operation(self, operation: str, path: str) -> SecurityDecision:
        """Check if a file operation is allowed.

        Args:
            operation: 'read', 'write', or 'edit'
            path: Target file path (will be resolved to absolute)
        """
        abs_path = Path(path).resolve()

        if operation in ("write", "edit") and self.mode == "workspace-write":
            if not self._is_within_workspace(abs_path):
                return SecurityDecision(
                    allowed=False,
                    reason=f"路径 {path} 在工作区外，当前模式 ({self.mode}) 不允许",
                )

        if operation in ("write", "edit") and self.mode == "read-only":
            return SecurityDecision(allowed=False, reason="当前为只读模式，不允许写操作")

        if self._is_protected_path(abs_path):
            return SecurityDecision(allowed=False, reason=f"路径 {path} 受保护")

        return SecurityDecision(allowed=True)

    def _sanitize_env(self) -> dict:
        """Remove sensitive environment variables. Called once in __init__."""
        protected = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "PRIVATE_KEY")
        return {
            k: v
            for k, v in os.environ.items()
            if not any(p in k.upper() for p in protected)
        }

    def _is_within_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(Path(self.workspace_dir).resolve())
            return True
        except ValueError:
            return False

    def _is_protected_path(self, path: Path) -> bool:
        protected = [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/sudoers",
            "/.ssh/",
            "/.gnupg/",
            "/.env",
            "/credentials",
            "/.aws/",
        ]
        path_str = str(path)
        return any(p in path_str for p in protected)
