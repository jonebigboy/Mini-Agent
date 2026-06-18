"""Path utilities for session and memory storage.

All persistent state lives under ~/.mini-agent/projects/<encoded-cwd>/.
The encoded-cwd is the absolute path with '/' replaced by '-' (leading
dash stripped). This mirrors Claude Code's session layout and survives
project moves/renames better than a hash.
"""
from pathlib import Path


def encode_cwd(cwd: str) -> str:
    """Encode an absolute path as a directory name.

    '/Users/jone/foo' → 'Users-jone-foo'
    Underscores and other characters are preserved.
    """
    return cwd.replace("/", "-").lstrip("-")


def project_dir(cwd: str) -> Path:
    """~/.mini-agent/projects/<encoded-cwd>/"""
    return Path.home() / ".mini-agent" / "projects" / encode_cwd(cwd)


def sessions_dir(cwd: str) -> Path:
    return project_dir(cwd) / "sessions"


def tool_outputs_dir(cwd: str, session_id: str) -> Path:
    return project_dir(cwd) / "tool_outputs" / session_id


def memory_dir(cwd: str) -> Path:
    return project_dir(cwd) / "memory"


def migration_marker_path() -> Path:
    """~/.mini-agent/.projects-v2-migrated — set after first migration run."""
    return Path.home() / ".mini-agent" / ".projects-v2-migrated"


def migration_log_path() -> Path:
    """~/.mini-agent/migration.log — append-only audit log."""
    return Path.home() / ".mini-agent" / "migration.log"
