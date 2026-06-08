"""Tests for security data models."""

from mini_agent.security.models import RiskLevel, SecurityDecision


def test_risk_level_ordering():
    """RiskLevel values support comparison: DENY > CONFIRM > SAFE."""
    assert RiskLevel.DENY > RiskLevel.CONFIRM
    assert RiskLevel.CONFIRM > RiskLevel.SAFE
    assert max(RiskLevel.SAFE, RiskLevel.DENY) == RiskLevel.DENY


def test_security_decision_allowed():
    """SecurityDecision with allowed=True has empty defaults."""
    d = SecurityDecision(allowed=True)
    assert d.allowed
    assert d.reason == ""
    assert d.command == ""
    assert d.env == {}


def test_security_decision_denied():
    """SecurityDecision with allowed=False carries reason."""
    d = SecurityDecision(allowed=False, reason="blocked")
    assert not d.allowed
    assert d.reason == "blocked"


def test_security_decision_with_env():
    """SecurityDecision can carry cleaned environment variables."""
    d = SecurityDecision(allowed=True, command="ls", env={"PATH": "/usr/bin"})
    assert d.env["PATH"] == "/usr/bin"
