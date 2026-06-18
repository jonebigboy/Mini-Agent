# tests/test_session_migrate.py
"""Tests for hash8 → encoded-cwd migration."""
import hashlib
import os
from pathlib import Path

import pytest

from mini_agent.paths import (
    migration_marker_path, migration_log_path, project_dir,
)
from mini_agent.session.migrate import (
    has_legacy_projects, migrate_all, _hash8_of,
)


def _make_legacy_dir(home: Path, project_path: Path) -> Path:
    """Create a legacy <name>_<hash8> dir under home/.mini-agent/projects/."""
    name = project_path.name
    hash8 = hashlib.sha256(str(project_path).encode()).hexdigest()[:8]
    legacy = home / ".mini-agent" / "projects" / f"{name}_{hash8}"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "memory" / "MEMORY.md").write_text("# legacy\n")
    return legacy


def test_hash8_of_known_path():
    assert _hash8_of("/Users/x/y") == hashlib.sha256(b"/Users/x/y").hexdigest()[:8]


def test_has_legacy_projects_false_when_empty(mini_agent_home):
    assert has_legacy_projects() is False


def test_has_legacy_projects_true(mini_agent_home, tmp_path):
    proj = tmp_path / "real_proj"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)
    assert has_legacy_projects() is True


def test_marker_file_skips_migration(mini_agent_home, tmp_path, monkeypatch):
    """Marker file present → migration skipped entirely."""
    proj = tmp_path / "p"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)
    migration_marker_path().parent.mkdir(parents=True, exist_ok=True)
    migration_marker_path().touch()

    monkeypatch.setenv("MINI_AGENT_FORCE_MIGRATE", "")  # not set
    migrated, orphans = migrate_all(cwd=str(proj))
    assert migrated == 0


def test_force_migrate_overrides_marker(mini_agent_home, tmp_path, monkeypatch):
    proj = tmp_path / "p2"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)
    migration_marker_path().parent.mkdir(parents=True, exist_ok=True)
    migration_marker_path().touch()

    monkeypatch.setenv("MINI_AGENT_FORCE_MIGRATE", "1")
    migrated, orphans = migrate_all(cwd=str(proj))
    assert migrated == 1


def test_no_migrate_env_skips(mini_agent_home, tmp_path, monkeypatch):
    proj = tmp_path / "p3"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)

    monkeypatch.setenv("MINI_AGENT_NO_MIGRATE", "1")
    migrated, orphans = migrate_all(cwd=str(proj))
    assert migrated == 0


def test_cwd_fast_path_migration(mini_agent_home, tmp_path):
    """Migration should find the cwd's legacy dir immediately via hash8 lookup."""
    proj = tmp_path / "fast_proj"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)

    migrated, orphans = migrate_all(cwd=str(proj))
    assert migrated == 1
    assert orphans == 0

    # New location exists
    new_dir = project_dir(str(proj)) / "memory"
    assert new_dir.exists()
    assert (new_dir / "MEMORY.md").read_text() == "# legacy\n"


def test_orphan_preserved_when_no_match(mini_agent_home, tmp_path):
    """Legacy dir with no matching path on disk → orphan, not deleted."""
    # Create a fake legacy dir that won't match any real path
    legacy = mini_agent_home / ".mini-agent" / "projects" / "ghost_deadbeef"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "memory" / "MEMORY.md").write_text("# ghost\n")

    migrated, orphans = migrate_all(cwd=str(tmp_path / "unrelated"))
    assert migrated == 0
    assert orphans == 1
    # Orphan dir still exists
    assert legacy.exists()


def test_existing_target_skips_migration(mini_agent_home, tmp_path):
    """If new-format target already exists, skip and don't overwrite."""
    proj = tmp_path / "p4"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)

    # Pre-create the new location
    new_memory = project_dir(str(proj)) / "memory"
    new_memory.mkdir(parents=True)
    (new_memory / "MEMORY.md").write_text("# already migrated\n")

    migrated, orphans = migrate_all(cwd=str(proj))
    assert migrated == 0
    # New content preserved
    assert (new_memory / "MEMORY.md").read_text() == "# already migrated\n"


def test_marker_created_after_migration(mini_agent_home, tmp_path):
    proj = tmp_path / "p5"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)
    assert not migration_marker_path().exists()

    migrate_all(cwd=str(proj))
    assert migration_marker_path().exists()


def test_migration_log_appended(mini_agent_home, tmp_path):
    proj = tmp_path / "p6"
    proj.mkdir()
    _make_legacy_dir(mini_agent_home, proj)
    migrate_all(cwd=str(proj))
    log = migration_log_path().read_text()
    assert "Migrated" in log
    assert proj.name in log
