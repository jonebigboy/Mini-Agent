"""Persistent rule storage for user-confirmed commands."""

import json
from datetime import datetime
from pathlib import Path


class UserConfirmation:
    """Handle persistent rule storage for confirmed commands."""

    def __init__(self, rules_path: str):
        self.rules_path = Path(rules_path).expanduser()
        self.user_rules = self._load_rules()

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
