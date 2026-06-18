# mini_agent/session/reader.py
"""SessionReader — load session JSONL into Message lists.

Two modes:
- raw (audit/debug): replay every original message event
- compacted (default for resume): skip summarized_seqs, keep summary messages

Robustness contract (CRITICAL):
- Partial last line (crash mid-write) -> truncate, warn, continue
- Missing external_ref file -> placeholder + warn, continue
- sha256 mismatch -> placeholder + warn, continue
- Unknown event type -> warn + skip, continue
- NEVER propagate these errors to the Agent
"""
import hashlib
import json
import warnings
from pathlib import Path
from typing import Iterator, Literal

from ..paths import sessions_dir, project_dir
from ..schema import Message, ToolCall
from .events import SessionEvent


class SessionNotFoundError(Exception):
    pass


class SessionReader:
    def __init__(self, cwd: str):
        self._cwd = cwd
        self._sessions_dir = sessions_dir(cwd)

    # -- public API --

    def load_messages(
        self,
        session_id: str,
        mode: Literal["raw", "compacted"] = "compacted",
    ) -> list[Message]:
        """Load messages from a session JSONL file."""
        events = list(self._iter_events(session_id))

        if mode == "raw":
            return [
                self._event_to_message(e)
                for e in events
                if e.type == "message"
            ]

        # compacted: collect all summarized_seqs, skip them
        summarized: set[int] = set()
        for e in events:
            if e.type == "summary_event" and e.summarized_seqs:
                summarized.update(e.summarized_seqs)

        return [
            self._event_to_message(e)
            for e in events
            if e.type == "message" and e.seq not in summarized
        ]

    def iter_events(self, session_id: str) -> Iterator[SessionEvent]:
        """Public accessor for raw iteration (used by `session cat`)."""
        return self._iter_events(session_id)

    def session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

    # -- internals --

    def _iter_events(self, session_id: str) -> Iterator[SessionEvent]:
        path = self.session_path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"No session file: {path}")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # Partial last line -- truncate, warn, stop iteration
                    warnings.warn(
                        f"session {session_id}: malformed line, truncated"
                    )
                    return
                try:
                    yield SessionEvent.model_validate(data)
                except Exception as exc:
                    # Unknown type / bad schema -- warn + skip
                    warnings.warn(
                        f"session {session_id}: skipping event seq={data.get('seq')}: {exc}"
                    )
                    continue

    def _event_to_message(self, event: SessionEvent) -> Message:
        content = self._resolve_content(event.content, event.session_id, event.seq)
        tool_calls = self._coerce_tool_calls(event.tool_calls, event.session_id, event.seq)
        return Message(
            role=event.role or "user",
            content=content if content is not None else "",
            thinking=event.thinking,
            tool_calls=tool_calls,
            tool_call_id=event.tool_call_id,
            name=event.name,
        )

    @staticmethod
    def _coerce_tool_calls(
        raw: list[dict] | None,
        session_id: str,
        seq: int,
    ) -> list[ToolCall] | None:
        """Coerce raw dict tool_calls into ToolCall instances.

        Event stores tool_calls as loose dicts (forward-compat with LLM schema
        drift). Message requires ToolCall pydantic objects. If a dict is missing
        required fields, warn + skip that single call rather than failing the
        whole message.
        """
        if raw is None:
            return None
        result: list[ToolCall] = []
        for tc in raw:
            try:
                result.append(ToolCall.model_validate(tc))
            except Exception as exc:
                warnings.warn(
                    f"session {session_id} seq {seq}: skipping malformed tool_call: {exc}"
                )
        return result if result else None

    def _resolve_content(
        self,
        content: str | list[dict] | None,
        session_id: str,
        seq: int,
    ) -> str | list[dict] | None:
        if not isinstance(content, list):
            return content
        result = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "external_ref":
                result.append(self._resolve_external_ref(block, session_id, seq))
            else:
                result.append(block)
        return result

    def _resolve_external_ref(
        self, block: dict, session_id: str, seq: int
    ) -> dict:
        """Read external_ref file, verify sha256. On any failure, return placeholder."""
        rel_path = block["path"]
        full_path = project_dir(self._cwd) / rel_path
        try:
            data = full_path.read_bytes()
        except OSError as exc:
            warnings.warn(
                f"session {session_id} seq {seq}: external_ref file missing: {rel_path}: {exc}"
            )
            return {"type": "text", "text": "[tool_result unavailable: file missing]"}

        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != block["sha256"]:
            warnings.warn(
                f"session {session_id} seq {seq}: external_ref sha256 mismatch for {rel_path}"
            )
            return {"type": "text", "text": "[tool_result unavailable: checksum mismatch]"}

        return {"type": "text", "text": data.decode("utf-8", errors="replace")}
