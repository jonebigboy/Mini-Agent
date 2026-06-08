"""Tests for SecurityMiddleware."""

import os
import pytest

from mini_agent.config import SecurityConfig
from mini_agent.security.middleware import SecurityMiddleware


def _make_middleware(**kwargs) -> SecurityMiddleware:
    """Create a middleware instance with default SecurityConfig overrides."""
    config = SecurityConfig(**kwargs)
    return SecurityMiddleware(config, "/workspace", interactive=True)


class TestCheckCommand:
    @pytest.mark.asyncio
    async def test_deny_command(self):
        m = _make_middleware()
        d = await m.check_command("rm -rf /")
        assert not d.allowed
        assert d.reason

    @pytest.mark.asyncio
    async def test_safe_command(self):
        m = _make_middleware()
        d = await m.check_command("git status")
        assert d.allowed

    @pytest.mark.asyncio
    async def test_safe_command_returns_env(self):
        m = _make_middleware()
        d = await m.check_command("git status")
        assert d.allowed
        assert isinstance(d.env, dict)

    @pytest.mark.asyncio
    async def test_non_interactive_deny(self):
        config = SecurityConfig(non_interactive_fallback="deny")
        m = SecurityMiddleware(config, "/workspace", interactive=False)
        d = await m.check_command("sudo apt update")
        assert not d.allowed

    @pytest.mark.asyncio
    async def test_non_interactive_allow(self):
        config = SecurityConfig(non_interactive_fallback="allow")
        m = SecurityMiddleware(config, "/workspace", interactive=False)
        d = await m.check_command("sudo apt update")
        assert d.allowed


class TestCheckFileOperation:
    @pytest.mark.asyncio
    async def test_write_within_workspace(self):
        m = _make_middleware()
        d = await m.check_file_operation("write", "/workspace/test.py")
        assert d.allowed

    @pytest.mark.asyncio
    async def test_write_outside_workspace(self):
        m = _make_middleware()
        d = await m.check_file_operation("write", "/etc/passwd")
        assert not d.allowed

    @pytest.mark.asyncio
    async def test_read_outside_workspace_allowed(self):
        m = _make_middleware()
        d = await m.check_file_operation("read", "/etc/hosts")
        assert d.allowed

    @pytest.mark.asyncio
    async def test_read_only_mode_blocks_write(self):
        config = SecurityConfig(mode="read-only")
        m = SecurityMiddleware(config, "/workspace", interactive=True)
        d = await m.check_file_operation("write", "/workspace/test.py")
        assert not d.allowed

    @pytest.mark.asyncio
    async def test_protected_path(self):
        m = _make_middleware()
        d = await m.check_file_operation("write", "/workspace/.env")
        assert not d.allowed
        assert "受保护" in d.reason

    @pytest.mark.asyncio
    async def test_protected_ssh_path(self):
        m = _make_middleware()
        d = await m.check_file_operation("write", "/workspace/.ssh/config")
        assert not d.allowed


class TestSanitizeEnv:
    def test_removes_sensitive_vars(self):
        os.environ["MY_API_KEY"] = "secret123"
        try:
            m = _make_middleware()
            assert "MY_API_KEY" not in m._clean_env
        finally:
            del os.environ["MY_API_KEY"]

    def test_keeps_safe_vars(self):
        m = _make_middleware()
        if "PATH" in os.environ:
            assert "PATH" in m._clean_env
