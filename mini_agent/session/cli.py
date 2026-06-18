# mini_agent/session/cli.py
"""`mini-agent session <subcommand>` handlers.

Invoked from cli.py main() when args.command == "session".
"""
import shutil
import sys
import time

from ..paths import tool_outputs_dir
from .events import SessionEvent
from .locker import FileLock
from .reader import SessionReader
from .store import SessionStore


def handle_session_command(args, cwd: str) -> int:
    """Dispatch session subcommand. Returns exit code."""
    action = getattr(args, "session_action", None)
    if action == "list":
        return _cmd_list(cwd)
    if action == "cat":
        return _cmd_cat(cwd, args.session_id, args.format, args.expand_external)
    if action == "tail":
        return _cmd_tail(cwd, args.session_id, follow=args.follow, n=args.n)
    if action == "rm":
        return _cmd_rm(cwd, args.session_id)
    print(f"unknown session action: {action}", file=sys.stderr)
    return 2


def _cmd_list(cwd: str) -> int:
    store = SessionStore(cwd)
    metas = store.list_sessions()
    if not metas:
        print("(no sessions)")
        return 0
    print(f"{'ID':<14} {'Started':<21} {'Status':<11} {'Msgs':<5} Preview")
    for m in metas[:20]:
        status = "● in-prog" if not m.ended else "✓ ended"
        started = (m.started_at or "")[:19]
        preview = (m.preview or "")[:40]
        print(f"{m.session_id[:14]:<14} {started:<21} {status:<11} {m.message_count:<5} {preview}")
    if len(metas) > 20:
        print(f"... and {len(metas) - 20} more")
    return 0


def _cmd_cat(cwd: str, sid: str, fmt: str, expand: bool) -> int:
    reader = SessionReader(cwd)
    try:
        events = list(reader.iter_events(sid))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if fmt == "json":
        for e in events:
            print(e.model_dump_json())
        return 0

    # text format
    print(f"Session: {sid}")
    if events:
        first = events[0]
        if first.type == "session_start":
            print(f"Started: {first.ts}")
            if first.cwd:
                print(f"Cwd:     {first.cwd}")
            if first.model:
                print(f"Model:   {first.model}")
            if first.forked_from:
                print(f"Forked:  {first.forked_from}")
    print()
    print("─" * 64)

    for e in events:
        ts_short = e.ts[11:19] if len(e.ts) >= 19 else e.ts
        if e.type == "session_start":
            continue  # already printed above
        elif e.type == "session_end":
            print(f"\n[seq {e.seq}] END ({e.end_reason})")
        elif e.type == "summary_event":
            print(f"\n[seq {e.seq}] SUMMARY_EVENT ({ts_short})")
            print(
                f"  summarized: {e.summarized_seqs}  →  summary at seq {e.summary_seq}"
            )
            if e.tokens_before is not None:
                print(f"  tokens: {e.tokens_before} → {e.tokens_after}")
        elif e.type == "message":
            label = (e.role or "?").upper()
            extra = f"  {e.name}" if e.name else ""
            print(f"\n[seq {e.seq}] {label}{extra:<15} ({ts_short})")
            if isinstance(e.content, str):
                _print_indented(e.content)
            elif isinstance(e.content, list):
                for block in e.content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        _print_indented(block.get("text", ""))
                    elif block.get("type") == "external_ref":
                        if expand:
                            print(
                                f"  [external_ref size={block.get('size')} "
                                f"sha256={block.get('sha256', '')[:12]}...]"
                            )
                        else:
                            print(
                                f"  [external_ref size={block.get('size')} "
                                f"sha256={block.get('sha256', '')[:12]}...]"
                            )
            if e.thinking:
                print(f"  thinking: {e.thinking}")
            if e.tool_calls:
                print(f"  tool_calls:")
                for tc in e.tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    print(
                        f"    [{tc.get('id') if isinstance(tc, dict) else None}] "
                        f"{fn.get('name')}({fn.get('arguments', '')})"
                    )
        elif e.type == "error":
            print(f"\n[seq {e.seq}] ERROR: {e.error}")
    return 0


def _print_indented(text: str) -> None:
    for line in (text.splitlines() or [""]):
        print(f"  {line}")


def _cmd_tail(cwd: str, sid: str, follow: bool, n: int) -> int:
    reader = SessionReader(cwd)
    path = reader.session_path(sid)
    if not path.exists():
        print(f"Error: session not found: {sid}", file=sys.stderr)
        return 1

    all_lines = path.read_text().splitlines()
    for line in all_lines[-n:]:
        try:
            e = SessionEvent.model_validate_json(line)
            print(f"[{e.seq}] {e.type:<14} {e.ts}")
        except Exception:
            print(f"[?] malformed: {line[:80]}")

    if not follow:
        return 0

    lockfile = path.parent / f"{sid}.lock"
    last_size = path.stat().st_size
    seen_end = any('"type":"session_end"' in l for l in all_lines)

    while True:
        try:
            time.sleep(0.2)
        except KeyboardInterrupt:
            return 0

        if not path.exists():
            print("[tail] session file removed", file=sys.stderr)
            return 0

        try:
            new_size = path.stat().st_size
        except OSError:
            return 0
        if new_size < last_size:
            print("[tail] file truncated", file=sys.stderr)
            return 0
        if new_size == last_size:
            if int(time.time()) % 5 == 0:
                try:
                    if not FileLock(lockfile).probe() and not seen_end:
                        print(
                            "[tail] writer seems crashed, tail stopped",
                            file=sys.stderr,
                        )
                        return 1
                except OSError:
                    pass
            continue

        with open(path, "r", encoding="utf-8") as f:
            f.seek(last_size)
            new_text = f.read()
        last_size = new_size

        for line in new_text.splitlines():
            if not line:
                continue
            try:
                e = SessionEvent.model_validate_json(line)
                print(f"[{e.seq}] {e.type:<14} {e.ts}")
                if e.type == "session_end":
                    return 0
            except Exception:
                # Partial line — wait for completion
                continue


def _cmd_rm(cwd: str, sid: str) -> int:
    store = SessionStore(cwd)
    path = store.session_path(sid)
    if not path.exists():
        print(f"Error: session not found: {sid}", file=sys.stderr)
        return 1
    try:
        path.unlink()
        tout = tool_outputs_dir(cwd, sid)
        if tout.exists():
            shutil.rmtree(tout, ignore_errors=True)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Removed: {sid}")
    return 0
