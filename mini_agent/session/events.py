# mini_agent/session/events.py
"""SessionEvent schema — one line in a session JSONL file.

Design:
- Single flat pydantic model with type discriminator (not discriminated union).
- `type` field determines which optional fields are expected.
- ExternalRefBlock is the only strictly-typed content sub-block (added new,
  no existing consumers to break). Text/tool_use/tool_result blocks remain
  loose dicts to avoid touching Message internals.
"""
from typing import Literal, Any
from pydantic import BaseModel, Field


class ExternalRefBlock(BaseModel):
    """Reference to an external file storing a large tool_result.

    Used when a tool_result's text content exceeds 10 KB; the body is written
    to tool_outputs/<sid>/seq-<N>.txt and the JSONL line carries only this
    pointer + integrity hash.
    """
    type: Literal["external_ref"] = "external_ref"
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class SessionEvent(BaseModel):
    """One line in a session JSONL file.

    See spec §5 for full schema documentation.
    """
    # Common header
    seq: int = Field(ge=0)
    ts: str  # ISO 8601
    type: Literal[
        "session_start",
        "session_end",
        "message",
        "summary_event",
        "error",
    ]
    session_id: str

    # message
    role: str | None = None
    content: str | list[dict[str, Any]] | None = None
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    # session_start
    cwd: str | None = None
    workspace: str | None = None
    model: str | None = None
    cli_args: dict[str, Any] | None = None
    schema_version: str | None = None
    forked_from: str | None = None

    # session_end
    end_reason: str | None = None

    # summary_event
    summarized_seqs: list[int] | None = None
    summary_seq: int | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None

    # error
    error: str | None = None
