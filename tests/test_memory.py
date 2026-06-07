"""Test cases for MemoryManager."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from mini_agent.tools.memory_manager import MemoryManager


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    return tmp_path / "test_project"


def test_resolve_memory_dir(temp_workspace):
    """Test memory directory path resolution with hash suffix."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    hash8 = hashlib.sha256(str(temp_workspace).encode()).hexdigest()[:8]
    expected = Path.home() / ".mini-agent" / "projects" / f"test_project_{hash8}" / "memory"
    assert manager.memory_dir == expected


def test_resolve_memory_dir_no_collision(tmp_path):
    """Test that paths with similar structure don't collide."""
    # These would collide with simple underscore escaping
    path_a = tmp_path / "a_b"
    path_b = tmp_path / "a" / "b"
    path_a.mkdir(parents=True)
    path_b.mkdir(parents=True)

    manager_a = MemoryManager(path_a)
    manager_b = MemoryManager(path_b)
    assert manager_a.memory_dir != manager_b.memory_dir


def test_ensure_memory_dir(temp_workspace):
    """Test that memory directory is created."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    assert not manager.memory_dir.exists()
    manager.ensure_memory_dir()
    assert manager.memory_dir.exists()
    assert manager.memory_dir.is_dir()


def test_load_memory_index_not_exists(temp_workspace):
    """Test loading when MEMORY.md doesn't exist."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    assert manager.load_memory_index() is None


def test_load_memory_index_exists(temp_workspace):
    """Test loading existing MEMORY.md."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    content = "# Test Memory\n\nSome important notes."
    (manager.memory_dir / "MEMORY.md").write_text(content)

    result = manager.load_memory_index()
    assert result == content


def test_load_memory_index_truncation(temp_workspace):
    """Test that MEMORY.md is truncated at 200 lines."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    lines = [f"Line {i}" for i in range(300)]
    (manager.memory_dir / "MEMORY.md").write_text("\n".join(lines))

    result = manager.load_memory_index()
    result_lines = result.splitlines()
    assert len(result_lines) == 202  # 200 lines + truncation notice (2 lines)
    assert "truncated" in result_lines[-1]
    assert "300" in result_lines[-1]


def test_load_project_instructions_not_exists(temp_workspace):
    """Test loading when MINI_AGENT.md doesn't exist."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    assert manager.load_project_instructions() is None


def test_load_project_instructions_exists(temp_workspace):
    """Test loading existing MINI_AGENT.md."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    content = "# Project Rules\n\nAlways use async/await."
    (temp_workspace / "MINI_AGENT.md").write_text(content)

    result = manager.load_project_instructions()
    assert result == content


def test_build_memory_context_empty(temp_workspace):
    """Test context when no memory files exist."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    assert manager.build_memory_context() == ""


def test_build_memory_context_with_both(temp_workspace):
    """Test context with both MINI_AGENT.md and MEMORY.md."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    (temp_workspace / "MINI_AGENT.md").write_text("# Project Rules\nUse async.")
    (manager.memory_dir / "MEMORY.md").write_text("# Memory Index\nTopic A")

    context = manager.build_memory_context()
    assert "Cross-Session Memory" in context
    assert "Project Instructions" in context
    assert "Persistent Memory Index" in context
    assert "Use async." in context
    assert "Topic A" in context
    assert str(manager.memory_dir) in context


def test_initialize_memory(temp_workspace):
    """Test full initialization flow."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)

    context = manager.initialize_memory()
    assert manager.memory_dir.exists()
    assert context == ""  # No files yet


def test_initialize_memory_with_content(temp_workspace):
    """Test initialization with existing memory content."""
    temp_workspace.mkdir()

    (temp_workspace / "MINI_AGENT.md").write_text("# Rules")
    manager = MemoryManager(temp_workspace)

    context = manager.initialize_memory()
    assert manager.memory_dir.exists()
    assert "Rules" in context


# --- Git repo awareness tests ---


def test_project_root_git_repo(temp_workspace):
    """Test that project_root uses git repo root when available."""
    temp_workspace.mkdir()
    # Mock git to return a fake repo root
    mock_result = type("Result", (), {"returncode": 0, "stdout": "/fake/repo\n"})()
    with patch("mini_agent.tools.memory_manager.subprocess.run", return_value=mock_result):
        manager = MemoryManager(temp_workspace)
        assert manager.project_root == Path("/fake/repo")


def test_project_root_no_git(temp_workspace):
    """Test that project_root falls back to workspace_dir when not in git repo."""
    temp_workspace.mkdir()
    mock_result = type("Result", (), {"returncode": 128, "stdout": ""})()
    with patch("mini_agent.tools.memory_manager.subprocess.run", return_value=mock_result):
        manager = MemoryManager(temp_workspace)
        assert manager.project_root == temp_workspace


def test_project_root_git_not_installed(temp_workspace):
    """Test fallback when git is not installed."""
    temp_workspace.mkdir()
    with patch(
        "mini_agent.tools.memory_manager.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        manager = MemoryManager(temp_workspace)
        assert manager.project_root == temp_workspace


def test_subdirectory_shares_memory_with_repo_root(tmp_path):
    """Test that a subdirectory resolves to the same memory dir as repo root."""
    repo_root = tmp_path / "my_project"
    subdir = repo_root / "src" / "components"
    subdir.mkdir(parents=True)

    mock_result = type("Result", (), {"returncode": 0, "stdout": str(repo_root) + "\n"})()
    with patch("mini_agent.tools.memory_manager.subprocess.run", return_value=mock_result):
        manager_root = MemoryManager(repo_root)
        manager_subdir = MemoryManager(subdir)

    assert manager_root.memory_dir == manager_subdir.memory_dir


# --- Global instructions tests ---


def test_load_global_instructions_not_exists(temp_workspace):
    """Test loading when global MINI_AGENT.md doesn't exist."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    assert manager.load_global_instructions() is None


def test_load_global_instructions_exists(temp_workspace, tmp_path):
    """Test loading existing global MINI_AGENT.md."""
    temp_workspace.mkdir()
    global_file = tmp_path / ".mini-agent" / "MINI_AGENT.md"
    global_file.parent.mkdir(parents=True, exist_ok=True)
    global_file.write_text("# Global\nUse uv for all projects.")

    manager = MemoryManager(temp_workspace)
    with patch.object(MemoryManager, "GLOBAL_INSTRUCTION_FILE", global_file):
        result = manager.load_global_instructions()
    assert result == "# Global\nUse uv for all projects."


def test_build_memory_context_with_all_three(temp_workspace, tmp_path):
    """Test context with global + project + memory index."""
    temp_workspace.mkdir()
    manager = MemoryManager(temp_workspace)
    manager.ensure_memory_dir()

    global_file = tmp_path / ".mini-agent" / "MINI_AGENT.md"
    global_file.parent.mkdir(parents=True, exist_ok=True)
    global_file.write_text("# Global\nAlways use uv.")

    (temp_workspace / "MINI_AGENT.md").write_text("# Project\nUse async.")
    (manager.memory_dir / "MEMORY.md").write_text("# Memory\nTopic A")

    with patch.object(MemoryManager, "GLOBAL_INSTRUCTION_FILE", global_file):
        context = manager.build_memory_context()

    assert "Global User Preferences" in context
    assert "Project Instructions" in context
    assert "Persistent Memory Index" in context
    assert "Always use uv." in context
    assert "Use async." in context
    assert "Topic A" in context
    # Global should come before project in the context
    assert context.index("Global") < context.index("Project Instructions")
