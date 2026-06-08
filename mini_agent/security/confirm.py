"""Interactive user confirmation and rule persistence."""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path


class ConfirmResult(Enum):
    """User confirmation result."""

    ALLOW = "allow"
    DENY = "deny"
    ALWAYS = "always"


class UserConfirmation:
    """Handle user confirmation prompts and persistent rule storage."""

    def __init__(self, rules_path: str):
        self.rules_path = Path(rules_path).expanduser()
        self.user_rules = self._load_rules()

    async def confirm(self, command: str, reason: str) -> ConfirmResult:
        """Display confirmation prompt and wait for user input.

        Uses input() instead of prompt_toolkit to avoid coupling.
        """
        print(f"\n⚠️  危险命令检测: {command}")
        print(f"原因: {reason}")
        choice = input("[y] 执行一次  [n] 拒绝  [a] 始终允许此类命令 > ").strip().lower()
        if choice == "a":
            return ConfirmResult.ALWAYS
        elif choice == "y":
            return ConfirmResult.ALLOW
        return ConfirmResult.DENY

    def add_permanent_rule(self, pattern: str):
        """Add a permanent allow rule and persist to disk."""
        rule = {
            "pattern": pattern,
            "action": "allow",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.user_rules.append(rule)
        self._save_rules()

    def _load_rules(self) -> list[dict]:
        """Load user rules from JSON file. Returns [] on any error."""
        if not self.rules_path.exists():
            return []
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            return data.get("user_rules", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save_rules(self):
        """Persist user rules to JSON file."""
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"user_rules": self.user_rules}
        self.rules_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
