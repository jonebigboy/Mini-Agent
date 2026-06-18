# mini_agent/session/store.py
"""SessionStore — directory-level operations: list, find, fork, TTL cleanup.

Doesn't read/write events itself (that's Writer/Reader's job); this layer
handles metadata derivation and lifecycle management.
"""
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..paths import sessions_dir, tool_outputs_dir
from .events import SessionEvent
from .locker import FileLock


@dataclass
class SessionMeta:
    session_id: str
    started_at: str | None         # ISO ts of session_start
    last_event_at: str | None      # ISO ts of last event
    ended: bool
    end_reason: str | None
    message_count: int
    preview: str                   # last user message preview
    model: str | None
    forked_from: str | None


class SessionStore:
    def __init__(self, cwd: str):
        self._cwd = cwd
        self._dir = sessions_dir(cwd)

    def session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.jsonl"

    def list_sessions(self) -> list[SessionMeta]:
        if not self._dir.exists():
            return []
        metas = []
        for jsonl in self._dir.glob("*.jsonl"):
            meta = self._derive_meta(jsonl)
            if meta is not None:
                metas.append(meta)
        # Sort by started_at descending (newest first)
        metas.sort(key=lambda m: m.started_at or "", reverse=True)
        return metas

    def find_latest(self) -> SessionMeta | None:
        metas = self.list_sessions()
        return metas[0] if metas else None

    def find_in_progress(self, max_age_hours: int = 24) -> list[SessionMeta]:
        """Sessions that: lack session_end AND lock is acquirable AND ts within max_age."""
        cutoff = time.time() - max_age_hours * 3600
        result = []
        for meta in self.list_sessions():
            if meta.ended:
                continue
            # Lock acquirable = no live process holding
            lockfile = self._dir / f"{meta.session_id}.lock"
            if not FileLock(lockfile).probe():
                continue  # actively locked by live process
            # Last event ts within window
            if meta.last_event_at is None:
                continue
            try:
                last_ts = datetime.fromisoformat(
                    meta.last_event_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            if last_ts >= cutoff:
                result.append(meta)
        return result

    def fork_session(self, source_id: str, new_id: str) -> None:
        """Copy source JSONL to new file. Caller is responsible for opening a
        new SessionWriter with forked_from=source_id to record the fork event."""
        src = self.session_path(source_id)
        dst = self.session_path(new_id)
        if not src.exists():
            raise FileNotFoundError(f"source session not found: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Delete sessions older than max_age_days. Skips currently-locked sessions."""
        if not self._dir.exists():
            return 0
        cutoff = time.time() - max_age_days * 24 * 3600
        removed = 0
        for jsonl in self._dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            sid = jsonl.stem
            lockfile = self._dir / f"{sid}.lock"
            if not FileLock(lockfile).probe():
                continue  # locked by live process; skip
            try:
                jsonl.unlink()
                # Also remove tool_outputs
                tout = tool_outputs_dir(self._cwd, sid)
                if tout.exists():
                    shutil.rmtree(tout, ignore_errors=True)
                if lockfile.exists():
                    try:
                        lockfile.unlink()
                    except OSError:
                        pass
                removed += 1
            except OSError as exc:
                import warnings
                warnings.warn(f"could not delete {jsonl}: {exc}")
        return removed

    # -- internals --

    def _derive_meta(self, jsonl: Path) -> SessionMeta | None:
        """Read first + last few events to derive metadata without full load."""
        sid = jsonl.stem
        try:
            events = self._read_first_and_last(jsonl, max_events=200)
        except OSError:
            return None

        first = events[0] if events else None
        last = events[-1] if events else None
        if first is None:
            return None

        # last user message preview
        preview = ""
        message_count = 0
        for e in events:
            if e.type == "message":
                message_count += 1
                if e.role == "user" and isinstance(e.content, str):
                    preview = e.content[:40]

        ended = last is not None and last.type == "session_end"
        end_reason = last.end_reason if ended and last else None

        return SessionMeta(
            session_id=sid,
            started_at=first.ts if first else None,
            last_event_at=last.ts if last else None,
            ended=ended,
            end_reason=end_reason,
            message_count=message_count,
            preview=preview,
            model=first.model if first else None,
            forked_from=first.forked_from if first else None,
        )

    def _read_first_and_last(
        self, jsonl: Path, max_events: int = 200
    ) -> list[SessionEvent]:
        """Read first N + last N events. For preview/metadata only --
        reader.py is the source of truth for full replay."""
        import json as _json
        events: list[SessionEvent] = []
        with open(jsonl, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    data = _json.loads(line)
                except _json.JSONDecodeError:
                    continue  # partial last line -- skip
                try:
                    events.append(SessionEvent.model_validate(data))
                except Exception:
                    continue
                if len(events) >= max_events:
                    break
        return events
