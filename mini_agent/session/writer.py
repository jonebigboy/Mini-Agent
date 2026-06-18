# mini_agent/session/writer.py
"""SessionWriter — append-only JSONL event writer.

One instance per Agent. Replaces the old AgentLogger. Writes one JSONL line
per event with os.fsync after each (crash recovery contract: at worst the
last event is lost).
"""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import sessions_dir, tool_outputs_dir
from .events import SessionEvent
from .locker import FileLock, SessionLockedError

# Per-block size threshold for external_ref externalization (10 KB)
EXTERNAL_REF_THRESHOLD = 10 * 1024

# Key names whose values must be redacted from cli_args
SECRET_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|token|secret|password|credential|auth)",
    re.IGNORECASE,
)


def _redact_secrets(value: Any) -> Any:
    """Recursively replace values of secret-looking keys with '***'."""
    if isinstance(value, dict):
        return {
            k: ("***" if SECRET_KEY_PATTERNS.search(k) else _redact_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SessionWriter:
    """Append-only event writer. One per Agent instance."""

    def __init__(
        self,
        session_id: str,
        cwd: str,
        workspace: str,
        model: str,
        cli_args: dict[str, Any],
    ):
        self.session_id = session_id
        self._cwd = cwd
        self._workspace = workspace
        self._model = model
        self._cli_args = cli_args

        self._path = sessions_dir(cwd) / f"{session_id}.jsonl"
        self._tool_outputs_dir = tool_outputs_dir(cwd, session_id)
        self._seq = -1
        self._lock = FileLock(sessions_dir(cwd) / f"{session_id}.lock")
        self._fh = None
        self._opened = False

    # -- lifecycle --

    def open(self, forked_from: str | None = None) -> None:
        """Acquire lock, create file, write session_start event."""
        if self._opened:
            return
        self._lock.acquire(blocking=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")
        self._opened = True
        self._append_event(
            SessionEvent(
                seq=self._next_seq(),
                ts=_now_iso(),
                type="session_start",
                session_id=self.session_id,
                cwd=self._cwd,
                workspace=self._workspace,
                model=self._model,
                cli_args=_redact_secrets(self._cli_args),
                schema_version="1",
                forked_from=forked_from,
            )
        )

    def close(self, end_reason: str = "exit") -> None:
        """Write session_end, release lock, close file handle. Idempotent."""
        if not self._opened:
            return
        self._append_event(
            SessionEvent(
                seq=self._next_seq(),
                ts=_now_iso(),
                type="session_end",
                session_id=self.session_id,
                end_reason=end_reason,
            )
        )
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        self._lock.release()
        self._opened = False

    # -- append API --

    def append_user_message(self, content: str | list[dict]) -> int:
        return self._append_message(role="user", content=content)

    def append_assistant_message(
        self,
        content: str | list[dict] | None,
        thinking: str | None = None,
        tool_calls: list[dict] | None = None,
    ) -> int:
        return self._append_message(
            role="assistant",
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
        )

    def append_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str | list[dict],
    ) -> int:
        externalized = self._externalize_content(content)
        return self._append_message(
            role="tool",
            content=externalized,
            tool_call_id=tool_call_id,
            name=name,
        )

    def append_summary_event(
        self,
        summarized_seqs: list[int],
        summary_seq: int,
        tokens_before: int,
        tokens_after: int,
    ) -> int:
        return self._append_event(
            SessionEvent(
                seq=self._next_seq(),
                ts=_now_iso(),
                type="summary_event",
                session_id=self.session_id,
                summarized_seqs=list(summarized_seqs),
                summary_seq=summary_seq,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )
        )

    def append_error(self, error: str) -> int:
        return self._append_event(
            SessionEvent(
                seq=self._next_seq(),
                ts=_now_iso(),
                type="error",
                session_id=self.session_id,
                error=error,
            )
        )

    # -- internals --

    def _next_seq(self) -> int:
        """Monotonic. No allocate-without-write window (synchronous)."""
        self._seq += 1
        return self._seq

    def _append_message(
        self,
        role: str,
        content: str | list[dict] | None = None,
        thinking: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> int:
        return self._append_event(
            SessionEvent(
                seq=self._next_seq(),
                ts=_now_iso(),
                type="message",
                session_id=self.session_id,
                role=role,
                content=content,
                thinking=thinking,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                name=name,
            )
        )

    def _externalize_content(
        self, content: str | list[dict]
    ) -> str | list[dict]:
        """For list content, externalize any text block > 10 KB."""
        if isinstance(content, str):
            return content
        result = []
        for idx, block in enumerate(content):
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and len(block.get("text", "").encode("utf-8")) > EXTERNAL_REF_THRESHOLD
            ):
                result.append(self._write_external_ref(block["text"]))
            else:
                result.append(block)
        return result

    def _write_external_ref(self, text: str) -> dict:
        """Write large text to tool_outputs/<sid>/seq-<N>.txt; return ExternalRefBlock dict."""
        # NOTE: at call time _next_seq has not been consumed yet; use the upcoming seq
        upcoming_seq = self._seq + 1
        self._tool_outputs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._tool_outputs_dir / f"seq-{upcoming_seq}.txt"
        data = text.encode("utf-8")
        out_path.write_bytes(data)
        return {
            "type": "external_ref",
            "path": str(out_path.relative_to(self._tool_outputs_dir.parent.parent)),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _append_event(self, event: SessionEvent) -> int:
        if not self._opened or self._fh is None:
            raise RuntimeError("SessionWriter not opened")
        line = event.model_dump_json()
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        return event.seq

    @property
    def path(self) -> Path:
        return self._path
