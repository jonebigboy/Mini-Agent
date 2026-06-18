# mini_agent/session/migrate.py
"""Migrate legacy <name>_<hash8>/memory/ → <encoded-cwd>/memory/.

Triggered automatically on first startup of new version. Fast-path:
- Marker file present → skip entirely
- No legacy dirs → skip entirely
- cwd matches a legacy hash8 → migrate immediately
- Spotlight/locate lookup for other dirs
- Filesystem walk as last resort
"""
import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..paths import (
    migration_log_path, migration_marker_path, project_dir,
)

LEGACY_PATTERN = re.compile(r"_[0-9a-f]{8}$")
SEARCH_ROOTS_DEFAULT_DEPTH = 6
SEARCH_TIME_BUDGET_S = 5.0


def _hash8_of(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:8]


def has_legacy_projects() -> bool:
    """Fast check: any dir under ~/.mini-agent/projects matches <name>_<hash8>?"""
    projects = Path.home() / ".mini-agent" / "projects"
    if not projects.exists():
        return False
    try:
        for p in projects.iterdir():
            if p.is_dir() and LEGACY_PATTERN.search(p.name):
                return True
    except OSError:
        pass
    return False


def migrate_all(cwd: str) -> tuple[int, int]:
    """Main entry. Returns (migrated_count, orphan_count).

    Idempotent via marker file.
    """
    if os.environ.get("MINI_AGENT_NO_MIGRATE"):
        return 0, 0

    if migration_marker_path().exists() and not os.environ.get("MINI_AGENT_FORCE_MIGRATE"):
        return 0, 0

    if not has_legacy_projects():
        # Even if nothing to migrate, set marker so we don't check again
        _touch_marker()
        return 0, 0

    migrated = 0
    orphans = 0
    projects_dir = Path.home() / ".mini-agent" / "projects"

    # Step 1: cwd fast path
    cwd_path = Path(cwd).resolve()
    cwd_legacy = _try_cwd_fast_path(projects_dir, cwd_path)
    if cwd_legacy:
        if _migrate_one(cwd_legacy, cwd_path):
            migrated += 1

    # Step 2: scan remaining legacy dirs
    for legacy_dir in list(projects_dir.iterdir()):
        if not legacy_dir.is_dir():
            continue
        if not LEGACY_PATTERN.search(legacy_dir.name):
            continue
        if not legacy_dir.exists():  # already migrated in step 1
            continue

        match = _find_match_for_legacy(legacy_dir)
        if match is None:
            # Could be: zero match (orphan) OR false positive (encoded-cwd looks like legacy)
            if _looks_like_false_positive(legacy_dir):
                continue  # silent skip
            orphans += 1
            _log_orphan(legacy_dir)
            continue
        if _migrate_one(legacy_dir, match):
            migrated += 1

    _touch_marker()
    return migrated, orphans


def _try_cwd_fast_path(
    projects_dir: Path, cwd: Path
) -> Optional[Path]:
    """If cwd's legacy dir exists, return it."""
    name = cwd.name
    hash8 = _hash8_of(str(cwd))
    legacy = projects_dir / f"{name}_{hash8}"
    return legacy if legacy.exists() else None


def _find_match_for_legacy(legacy_dir: Path) -> Optional[Path]:
    """Find the real path that produced this legacy dir.

    Strategy: Spotlight (macOS) / locate (Linux) first, then filesystem walk.
    """
    name, _, hash8 = legacy_dir.name.rpartition("_")
    if not name or len(hash8) != 8:
        return None

    candidates = _spotlight_candidates(name)
    if not candidates:
        candidates = _locate_candidates(name)
    if not candidates:
        candidates = _walk_candidates(name)

    # Verify hash8
    matches = [
        c for c in candidates
        if _hash8_of(str(c)) == hash8
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Ambiguous — pick first and log
        _log(f"WARN Ambiguous legacy {legacy_dir.name}: {len(matches)} matches, using {matches[0]}")
        return matches[0]
    return None


def _spotlight_candidates(name: str) -> list[Path]:
    """macOS mdfind. Returns [] on non-macOS or failure."""
    if not shutil.which("mdfind"):
        return []
    try:
        out = subprocess.run(
            ["mdfind", "-name", name],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [
        Path(line)
        for line in out.splitlines()
        if Path(line).name == name and Path(line).is_dir()
    ]


def _locate_candidates(name: str) -> list[Path]:
    if not shutil.which("locate"):
        return []
    try:
        out = subprocess.run(
            ["locate", name],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [
        Path(line)
        for line in out.splitlines()
        if Path(line).name == name and Path(line).is_dir()
    ]


def _walk_candidates(name: str) -> list[Path]:
    """Last-resort filesystem walk."""
    deadline = time.time() + SEARCH_TIME_BUDGET_S
    home = Path.home()
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".cache"}
    found = []
    for root, dirs, files in os.walk(home):
        if time.time() > deadline:
            break
        # Filter dirs in place
        dirs[:] = [d for d in dirs if d not in skip]
        depth = Path(root).relative_to(home).parts
        if len(depth) > SEARCH_ROOTS_DEFAULT_DEPTH:
            dirs[:] = []
            continue
        if Path(root).name == name:
            found.append(Path(root))
    return found


def _looks_like_false_positive(legacy_dir: Path) -> bool:
    """A legacy-looking dir that's actually a new-format encoded-cwd.

    Heuristic: name contains multiple '-' segments (encoded paths usually do).
    And memory dir is empty.
    """
    name = legacy_dir.name
    if name.count("-") < 2:
        return False
    memory = legacy_dir / "memory"
    if not memory.exists():
        return False
    try:
        return not any(memory.iterdir())
    except OSError:
        return False


def _migrate_one(old_dir: Path, real_path: Path) -> bool:
    """Move old_dir/memory → project_dir(real_path)/memory.

    Returns True if migrated, False if skipped (target exists).
    """
    old_memory = old_dir / "memory"
    new_memory = project_dir(str(real_path)) / "memory"

    if new_memory.exists():
        _log(f"WARN Skipping {old_dir.name}: target {new_memory} already exists")
        return False

    if not old_memory.exists():
        # Nothing to migrate (legacy dir without memory/ subdir)
        return False

    new_memory.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(old_memory), str(new_memory))
    except OSError as exc:
        _log(f"ERROR Failed to move {old_memory}: {exc}")
        return False

    # Remove now-empty old_dir
    try:
        old_dir.rmdir()
    except OSError:
        pass  # not empty (other files?), leave for user

    _log(f"INFO Migrated {old_dir.name} → {new_memory.parent.name}/")
    return True


def _touch_marker() -> None:
    marker = migration_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _log(message: str) -> None:
    log_path = migration_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{ts} {message}\n")


def _log_orphan(legacy_dir: Path) -> None:
    _log(f"WARN Orphan: {legacy_dir.name} (no matching path found)")
