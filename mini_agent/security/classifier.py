"""Command classifier for security risk assessment."""

import re

from mini_agent.security.models import RiskLevel


# Built-in deny patterns (highest priority, always blocked)
BUILTIN_DENY = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"mkfs",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\{\s*:\|:&\s*\};:",  # fork bomb
]

# Built-in allow patterns (whitelist, auto-allow)
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

# Built-in confirm patterns (require user confirmation)
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


class CommandClassifier:
    """Classify commands by risk level."""

    def __init__(
        self,
        extra_allow: list[str] | None = None,
        extra_deny: list[str] | None = None,
        extra_confirm: list[str] | None = None,
    ):
        self._deny = BUILTIN_DENY + (extra_deny or [])
        self._allow = BUILTIN_ALLOW + (extra_allow or [])
        self._confirm = BUILTIN_CONFIRM + (extra_confirm or [])
        self._deny_re = [re.compile(p) for p in self._deny]
        self._allow_re = [re.compile(p) for p in self._allow]
        self._confirm_re = [re.compile(p) for p in self._confirm]

    def classify(
        self, command: str, mode: str, user_rules: list[dict]
    ) -> tuple[RiskLevel, str]:
        """Classify a command. Returns (RiskLevel, reason_str)."""
        # 1. User rules (highest priority)
        user_risk = self._check_user_rules(command, user_rules)
        if user_risk:
            return user_risk

        # 2. Split compound commands, classify each part, take max
        parts = self._split_compound(command)
        if len(parts) > 1:
            max_risk = RiskLevel.SAFE
            max_reason = ""
            for sub_cmd in parts:
                stripped = sub_cmd.strip()
                if not stripped:
                    continue
                risk, reason = self.classify(stripped, mode, user_rules)
                if risk > max_risk:
                    max_risk = risk
                    max_reason = reason
            return max_risk, max_reason

        # 3. Single command classification
        return self._classify_single(command, mode)

    def _check_user_rules(self, command: str, rules: list[dict]) -> tuple[RiskLevel, str] | None:
        """Check user-defined rules. Returns (RiskLevel, reason) or None."""
        for rule in rules:
            pattern = rule.get("pattern", "")
            action = rule.get("action", "")
            if not pattern or not action:
                continue
            try:
                if re.search(pattern, command):
                    if action == "allow":
                        return RiskLevel.SAFE, ""
                    elif action == "deny":
                        return RiskLevel.DENY, f"匹配用户拒绝规则: {pattern}"
            except re.error:
                continue
        return None

    def _split_compound(self, command: str) -> list[str]:
        """Split compound commands on |, ||, &&, ; respecting quotes and $().

        V1: Only handles top-level operators. Does not recurse into $()
        or handle backticks/complex escaping.
        """
        parts = []
        current = []
        i = 0
        in_single_quote = False
        in_double_quote = False
        paren_depth = 0

        while i < len(command):
            ch = command[i]

            # Track quote state
            if ch == "'" and not in_double_quote and paren_depth == 0:
                in_single_quote = not in_single_quote
                current.append(ch)
                i += 1
                continue
            if ch == '"' and not in_single_quote and paren_depth == 0:
                in_double_quote = not in_double_quote
                current.append(ch)
                i += 1
                continue

            # Track $() nesting
            if ch == "$" and i + 1 < len(command) and command[i + 1] == "(":
                paren_depth += 1
                current.append("$(")
                i += 2
                continue
            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                current.append(")")
                i += 1
                continue

            # Split on operators (only outside quotes and parens)
            if not in_single_quote and not in_double_quote and paren_depth == 0:
                # Check for || first (before |)
                if ch == "|" and i + 1 < len(command) and command[i + 1] == "|":
                    parts.append("".join(current))
                    current = []
                    i += 2
                    continue
                # Check for &&
                if ch == "&" and i + 1 < len(command) and command[i + 1] == "&":
                    parts.append("".join(current))
                    current = []
                    i += 2
                    continue
                # Check for single | or ;
                if ch in ("|", ";"):
                    parts.append("".join(current))
                    current = []
                    i += 1
                    continue

            current.append(ch)
            i += 1

        if current:
            parts.append("".join(current))

        return parts

    def _classify_single(self, command: str, mode: str) -> tuple[RiskLevel, str]:
        """Classify a single (non-compound) command."""
        # Deny check (highest priority)
        if self._matches(command, self._deny_re):
            return RiskLevel.DENY, "命令匹配拒绝规则"

        # Allow check (whitelist)
        if self._matches(command, self._allow_re):
            return RiskLevel.SAFE, ""

        # Confirm check
        if self._matches(command, self._confirm_re):
            return RiskLevel.CONFIRM, "命令需要确认"

        # Default: whitelist approach
        if mode == "full-access":
            return RiskLevel.SAFE, ""
        return RiskLevel.CONFIRM, "命令不在已知安全列表中"

    @staticmethod
    def _matches(command: str, patterns: list[re.Pattern]) -> bool:
        """Check if command matches any of the compiled patterns."""
        return any(p.search(command) for p in patterns)
