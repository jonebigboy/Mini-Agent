"""Tests for UserConfirmation and rule persistence."""

import json

from mini_agent.security.confirm import ConfirmResult, UserConfirmation


class TestRulePersistence:
    def test_load_rules_from_file(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "user_rules": [{"pattern": "docker", "action": "allow"}]
        }))
        uc = UserConfirmation(str(rules_file))
        assert len(uc.user_rules) == 1
        assert uc.user_rules[0]["pattern"] == "docker"

    def test_save_rules_creates_file(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        uc = UserConfirmation(str(rules_file))
        uc.add_permanent_rule("docker run")
        saved = json.loads(rules_file.read_text())
        assert len(saved["user_rules"]) == 1
        assert saved["user_rules"][0]["pattern"] == "docker run"
        assert saved["user_rules"][0]["action"] == "allow"

    def test_save_rules_appends(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        uc = UserConfirmation(str(rules_file))
        uc.add_permanent_rule("docker run")
        uc.add_permanent_rule("kubectl")
        saved = json.loads(rules_file.read_text())
        assert len(saved["user_rules"]) == 2

    def test_load_nonexistent_returns_empty(self, tmp_path):
        uc = UserConfirmation(str(tmp_path / "nonexistent.json"))
        assert uc.user_rules == []

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        rules_file.write_text("not json")
        uc = UserConfirmation(str(rules_file))
        assert uc.user_rules == []


class TestConfirmResult:
    def test_confirm_allow(self):
        assert ConfirmResult.ALLOW.value == "allow"

    def test_confirm_deny(self):
        assert ConfirmResult.DENY.value == "deny"

    def test_confirm_always(self):
        assert ConfirmResult.ALWAYS.value == "always"
