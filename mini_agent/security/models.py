"""Security data models."""

from dataclasses import dataclass, field
from enum import IntEnum


class RiskLevel(IntEnum):
    """Risk level for command classification. Higher value = more dangerous."""

    SAFE = 0  # Auto-allow
    CONFIRM = 1  # Require user confirmation
    DENY = 2  # Block unconditionally


@dataclass
class SecurityDecision:
    """Result of a security check.

    Single-field design: allowed=True means proceed, allowed=False means blocked.
    """

    allowed: bool
    reason: str = ""
    command: str = ""
    env: dict = field(default_factory=dict)
