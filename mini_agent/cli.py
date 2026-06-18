"""
Mini Agent - Interactive Runtime Example

Usage:
    mini-agent [--workspace DIR] [--task TASK]

Examples:
    mini-agent                              # Use current directory as workspace (interactive mode)
    mini-agent --workspace /path/to/dir     # Use specific workspace directory (interactive mode)
    mini-agent --task "create a file"       # Execute a task non-interactively
"""

import argparse
import asyncio
import platform
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import List

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from mini_agent import LLMClient
from mini_agent.agent import Agent
from mini_agent.session.writer import SessionWriter
from mini_agent.session.reader import SessionReader, SessionNotFoundError
from mini_agent.session.store import SessionStore
from mini_agent.session.locker import SessionLockedError
from mini_agent.commands.init_command import INIT_PROMPT_TEMPLATE
from mini_agent.config import Config
from mini_agent.schema import LLMProvider
from mini_agent.tools.base import Tool
from mini_agent.tools.bash_tool import BackgroundShellManager, BashKillTool, BashOutputTool, BashTool
from mini_agent.tools.file_tools import EditTool, ReadTool, WriteTool
from mini_agent.tools.mcp_loader import cleanup_mcp_connections, load_mcp_tools_async, set_mcp_timeout_config
from mini_agent.tools.memory_manager import MemoryManager
from mini_agent.tools.skill_tool import create_skill_tools
from mini_agent.utils import calculate_display_width
from mini_agent.security import SecurityMiddleware
from mini_agent.ui.confirm_selector import ConfirmSelector


# ANSI color codes
class Colors:
    """Terminal color definitions"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def print_banner():
    """Print welcome banner with proper alignment"""
    BOX_WIDTH = 58
    banner_text = f"{Colors.BOLD}🤖 Mini Agent - Multi-turn Interactive Session{Colors.RESET}"
    banner_width = calculate_display_width(banner_text)

    # Center the text with proper padding
    total_padding = BOX_WIDTH - banner_width
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding

    print()
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}╔{'═' * BOX_WIDTH}╗{Colors.RESET}")
    print(
        f"{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET}{' ' * left_padding}{banner_text}{' ' * right_padding}{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET}"
    )
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}╚{'═' * BOX_WIDTH}╝{Colors.RESET}")
    print()


def print_help():
    """Print help information"""
    help_text = f"""
{Colors.BOLD}{Colors.BRIGHT_YELLOW}Available Commands:{Colors.RESET}
  {Colors.BRIGHT_GREEN}/help{Colors.RESET}      - Show this help message
  {Colors.BRIGHT_GREEN}/clear{Colors.RESET}     - Clear session history (keep system prompt)
  {Colors.BRIGHT_GREEN}/history{Colors.RESET}   - Show current session message count
  {Colors.BRIGHT_GREEN}/stats{Colors.RESET}     - Show session statistics
  {Colors.BRIGHT_GREEN}/init{Colors.RESET}      - Scan project and generate AGENTS.md
  {Colors.BRIGHT_GREEN}/bg{Colors.RESET}        - 显示后台 shell 进程
  {Colors.BRIGHT_GREEN}/sessions{Colors.RESET}  - List sessions in current workspace
  {Colors.BRIGHT_GREEN}/session-id{Colors.RESET} - Show current session id + resume hint
  {Colors.BRIGHT_GREEN}/resume [id]{Colors.RESET} - Resume another session (soft-switch)
  {Colors.BRIGHT_GREEN}/fork [id]{Colors.RESET}  - Fork current or specified session
  {Colors.BRIGHT_GREEN}/exit{Colors.RESET}      - Exit program (also: exit, quit, q)

{Colors.BOLD}{Colors.BRIGHT_YELLOW}Keyboard Shortcuts:{Colors.RESET}
  {Colors.BRIGHT_CYAN}Esc{Colors.RESET}        - Cancel current agent execution
  {Colors.BRIGHT_CYAN}Ctrl+C{Colors.RESET}     - Exit program
  {Colors.BRIGHT_CYAN}Ctrl+U{Colors.RESET}     - Clear current input line
  {Colors.BRIGHT_CYAN}Ctrl+L{Colors.RESET}     - Clear screen
  {Colors.BRIGHT_CYAN}Ctrl+J{Colors.RESET}     - Insert newline (also Ctrl+Enter)
  {Colors.BRIGHT_CYAN}Tab{Colors.RESET}        - Auto-complete commands
  {Colors.BRIGHT_CYAN}↑/↓{Colors.RESET}        - Browse command history
  {Colors.BRIGHT_CYAN}→{Colors.RESET}          - Accept auto-suggestion

{Colors.BOLD}{Colors.BRIGHT_YELLOW}Usage:{Colors.RESET}
  - Enter your task directly, Agent will help you complete it
  - Agent remembers all conversation content in this session
  - Use {Colors.BRIGHT_GREEN}/clear{Colors.RESET} to start a new session
  - Press {Colors.BRIGHT_CYAN}Enter{Colors.RESET} to submit your message
  - Use {Colors.BRIGHT_CYAN}Ctrl+J{Colors.RESET} to insert line breaks within your message
"""
    print(help_text)


def print_session_info(agent: Agent, workspace_dir: Path, model: str):
    """Print session information with proper alignment"""
    BOX_WIDTH = 58

    def print_info_line(text: str):
        """Print a single info line with proper padding"""
        # Account for leading space
        text_width = calculate_display_width(text)
        padding = max(0, BOX_WIDTH - 1 - text_width)
        print(f"{Colors.DIM}│{Colors.RESET} {text}{' ' * padding}{Colors.DIM}│{Colors.RESET}")

    # Top border
    print(f"{Colors.DIM}┌{'─' * BOX_WIDTH}┐{Colors.RESET}")

    # Header (centered)
    header_text = f"{Colors.BRIGHT_CYAN}Session Info{Colors.RESET}"
    header_width = calculate_display_width(header_text)
    header_padding_total = BOX_WIDTH - 1 - header_width  # -1 for leading space
    header_padding_left = header_padding_total // 2
    header_padding_right = header_padding_total - header_padding_left
    print(f"{Colors.DIM}│{Colors.RESET} {' ' * header_padding_left}{header_text}{' ' * header_padding_right}{Colors.DIM}│{Colors.RESET}")

    # Divider
    print(f"{Colors.DIM}├{'─' * BOX_WIDTH}┤{Colors.RESET}")

    # Info lines
    print_info_line(f"Model: {model}")
    print_info_line(f"Workspace: {workspace_dir}")
    print_info_line(f"Message History: {len(agent.messages)} messages")
    print_info_line(f"Available Tools: {len(agent.tools)} tools")

    # Bottom border
    print(f"{Colors.DIM}└{'─' * BOX_WIDTH}┘{Colors.RESET}")
    print()
    print(f"{Colors.DIM}Type {Colors.BRIGHT_GREEN}/help{Colors.DIM} for help, {Colors.BRIGHT_GREEN}/exit{Colors.DIM} to quit{Colors.RESET}")
    print()


def print_stats(agent: Agent, session_start: datetime):
    """Print session statistics"""
    duration = datetime.now() - session_start
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count different types of messages
    user_msgs = sum(1 for m in agent.messages if m.role == "user")
    assistant_msgs = sum(1 for m in agent.messages if m.role == "assistant")
    tool_msgs = sum(1 for m in agent.messages if m.role == "tool")

    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}Session Statistics:{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 40}{Colors.RESET}")
    print(f"  Session Duration: {hours:02d}:{minutes:02d}:{seconds:02d}")
    print(f"  Total Messages: {len(agent.messages)}")
    print(f"    - User Messages: {Colors.BRIGHT_GREEN}{user_msgs}{Colors.RESET}")
    print(f"    - Assistant Replies: {Colors.BRIGHT_BLUE}{assistant_msgs}{Colors.RESET}")
    print(f"    - Tool Calls: {Colors.BRIGHT_YELLOW}{tool_msgs}{Colors.RESET}")
    print(f"  Available Tools: {len(agent.tools)}")
    if agent.api_total_tokens > 0:
        print(f"  API Tokens Used: {Colors.BRIGHT_MAGENTA}{agent.api_total_tokens:,}{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 40}{Colors.RESET}\n")


def _format_elapsed(seconds: float) -> str:
    """将秒数格式化为易读的时间字符串。"""
    if seconds >= 3600:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins}m"
    elif seconds >= 60:
        return f"{int(seconds // 60)}m"
    else:
        return f"{int(seconds)}s"


def _print_background_status():
    """打印后台 shell 进程状态（/bg 命令使用）。"""
    summaries = BackgroundShellManager.get_summary()
    if not summaries:
        print(f"\n{Colors.DIM}没有后台进程。{Colors.RESET}\n")
        return

    running = [s for s in summaries if s["status"] == "running"]
    stopped = [s for s in summaries if s["status"] != "running"]

    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}⚙️  后台进程{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

    if running:
        print(f"  {Colors.BRIGHT_GREEN}运行中 ({len(running)}):{Colors.RESET}")
        for s in running:
            time_str = _format_elapsed(s["elapsed"])
            cmd = s["command"][:40] + "..." if len(s["command"]) > 40 else s["command"]
            print(
                f"    {Colors.BRIGHT_YELLOW}{s['bash_id']}{Colors.RESET}"
                f"  {time_str:>5s}  {Colors.DIM}{cmd}{Colors.RESET}"
            )

    if stopped:
        print(f"  {Colors.DIM}已结束 ({len(stopped)}):{Colors.RESET}")
        for s in stopped:
            cmd = s["command"][:40] + "..." if len(s["command"]) > 40 else s["command"]
            print(
                f"    {s['bash_id']}  {s['status']:<10s}  {Colors.DIM}{cmd}{Colors.RESET}"
            )

    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}\n")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Mini Agent - AI assistant with file tools and MCP support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mini-agent                              # Use current directory as workspace
  mini-agent --workspace /path/to/dir     # Use specific workspace directory
  mini-agent --continue                   # Resume the most recent session
  mini-agent --resume <id-prefix>         # Resume a specific session
  mini-agent --fork <id-prefix>           # Fork a session (preserves original)
  mini-agent session list                 # List sessions in current workspace
  mini-agent session cat <id>             # Pretty-print a session's events
        """,
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=None,
        help="Workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--task",
        "-t",
        type=str,
        default=None,
        help="Execute a task non-interactively and exit",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="mini-agent 0.1.0",
    )

    # Top-level flags for session resume
    parser.add_argument(
        "--continue",
        action="store_true",
        dest="continue_latest",
        help="Resume the most recent session in the current directory",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        dest="resume_id",
        help="Resume a specific session by ID (supports prefix matching)",
    )
    parser.add_argument(
        "--fork",
        metavar="SESSION_ID",
        dest="fork_id",
        help="Fork a new session from an existing one (preserves original)",
    )
    parser.add_argument(
        "--any-workspace",
        action="store_true",
        dest="any_workspace",
        help="With --resume, search across all workspaces (default: strict to cwd)",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # session subcommand: cat/list/tail/rm (handlers wired in Task 15)
    session_parser = subparsers.add_parser("session", help="Manage sessions")
    session_sub = session_parser.add_subparsers(dest="session_action", required=False)

    session_sub.add_parser("list", help="List sessions")
    session_cat = session_sub.add_parser("cat", help="Print session contents")
    session_cat.add_argument("session_id")
    session_cat.add_argument("--format", choices=["text", "json"], default="text")
    session_cat.add_argument("--expand-external", action="store_true")

    session_tail = session_sub.add_parser("tail", help="Tail session events")
    session_tail.add_argument("session_id")
    session_tail.add_argument("-f", "--follow", action="store_true")
    session_tail.add_argument("-n", type=int, default=20)

    session_rm = session_sub.add_parser("rm", help="Delete a session")
    session_rm.add_argument("session_id")

    # migrate-orphans top-level
    subparsers.add_parser(
        "migrate-orphans", help="Inspect orphan legacy session dirs"
    )

    return parser.parse_args()


async def initialize_base_tools(config: Config):
    """Initialize base tools (independent of workspace)

    These tools are loaded from package configuration and don't depend on workspace.
    Note: File tools are now workspace-dependent and initialized in add_workspace_tools()

    Args:
        config: Configuration object

    Returns:
        Tuple of (list of tools, skill loader if skills enabled)
    """

    tools = []
    skill_loader = None

    # 1. Bash auxiliary tools (output monitoring and kill)
    # Note: BashTool itself is created in add_workspace_tools() with workspace_dir as cwd
    if config.tools.enable_bash:
        bash_output_tool = BashOutputTool()
        tools.append(bash_output_tool)
        print(f"{Colors.GREEN}✅ Loaded Bash Output tool{Colors.RESET}")

        bash_kill_tool = BashKillTool()
        tools.append(bash_kill_tool)
        print(f"{Colors.GREEN}✅ Loaded Bash Kill tool{Colors.RESET}")

    # 3. Claude Skills (loaded from package directory)
    if config.tools.enable_skills:
        print(f"{Colors.BRIGHT_CYAN}Loading Claude Skills...{Colors.RESET}")
        try:
            # Resolve skills directory with priority search
            # Expand ~ to user home directory for portability
            skills_path = Path(config.tools.skills_dir).expanduser()
            if skills_path.is_absolute():
                skills_dir = str(skills_path)
            else:
                # Search in priority order:
                # 1. Current directory (dev mode: ./skills or ./mini_agent/skills)
                # 2. Package directory (installed: site-packages/mini_agent/skills)
                search_paths = [
                    skills_path,  # ./skills for backward compatibility
                    Path("mini_agent") / skills_path,  # ./mini_agent/skills
                    Config.get_package_dir() / skills_path,  # site-packages/mini_agent/skills
                ]

                # Find first existing path
                skills_dir = str(skills_path)  # default
                for path in search_paths:
                    if path.exists():
                        skills_dir = str(path.resolve())
                        break

            skill_tools, skill_loader = create_skill_tools(skills_dir)
            if skill_tools:
                tools.extend(skill_tools)
                print(f"{Colors.GREEN}✅ Loaded Skill tool (get_skill){Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  No available Skills found{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  Failed to load Skills: {e}{Colors.RESET}")

    # 4. MCP tools (loaded with priority search)
    if config.tools.enable_mcp:
        print(f"{Colors.BRIGHT_CYAN}Loading MCP tools...{Colors.RESET}")
        try:
            # Apply MCP timeout configuration from config.yaml
            mcp_config = config.tools.mcp
            set_mcp_timeout_config(
                connect_timeout=mcp_config.connect_timeout,
                execute_timeout=mcp_config.execute_timeout,
                sse_read_timeout=mcp_config.sse_read_timeout,
            )
            print(
                f"{Colors.DIM}  MCP timeouts: connect={mcp_config.connect_timeout}s, "
                f"execute={mcp_config.execute_timeout}s, sse_read={mcp_config.sse_read_timeout}s{Colors.RESET}"
            )

            # Use priority search for mcp.json
            mcp_config_path = Config.find_config_file(config.tools.mcp_config_path)
            if mcp_config_path:
                mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
                if mcp_tools:
                    tools.extend(mcp_tools)
                    print(f"{Colors.GREEN}✅ Loaded {len(mcp_tools)} MCP tools (from: {mcp_config_path}){Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}⚠️  No available MCP tools found{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  MCP config file not found: {config.tools.mcp_config_path}{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  Failed to load MCP tools: {e}{Colors.RESET}")

    print()  # Empty line separator
    return tools, skill_loader


def _warn_legacy_files(workspace_dir: Path) -> None:
    """Print warnings for deprecated files from previous Mini-Agent versions."""
    legacy_global = Path.home() / ".mini-agent" / "MINI_AGENT.md"
    if legacy_global.exists():
        print(
            f"{Colors.YELLOW}⚠️  Detected {legacy_global}. "
            f"Please rename to AGENTS.md to match the new convention.{Colors.RESET}"
        )

    legacy_notes = workspace_dir / ".agent_memory.json"
    if legacy_notes.exists():
        print(
            f"{Colors.YELLOW}ℹ️  Found {legacy_notes} (legacy SessionNoteTool data). "
            f"Safe to delete — SessionNoteTool has been removed.{Colors.RESET}"
        )


def add_workspace_tools(tools: List[Tool], config: Config, workspace_dir: Path,
                        security: SecurityMiddleware | None = None):
    """Add workspace-dependent tools

    These tools need to know the workspace directory.

    Args:
        tools: Existing tools list to add to
        config: Configuration object
        workspace_dir: Workspace directory path
        security: Optional security middleware for tool checks
    """
    # Ensure workspace directory exists
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Bash tool - needs workspace as cwd for command execution
    if config.tools.enable_bash:
        bash_tool = BashTool(workspace_dir=str(workspace_dir), security=security)
        tools.append(bash_tool)
        print(f"{Colors.GREEN}✅ Loaded Bash tool (cwd: {workspace_dir}){Colors.RESET}")

    # File tools - need workspace to resolve relative paths
    if config.tools.enable_file_tools:
        tools.extend(
            [
                ReadTool(workspace_dir=str(workspace_dir)),
                WriteTool(workspace_dir=str(workspace_dir), security=security),
                EditTool(workspace_dir=str(workspace_dir), security=security),
            ]
        )
        print(f"{Colors.GREEN}✅ Loaded file operation tools (workspace: {workspace_dir}){Colors.RESET}")


async def _quiet_cleanup():
    """Clean up MCP connections, suppressing noisy asyncgen teardown tracebacks."""
    # Silence the asyncgen finalization noise that anyio/mcp emits when
    # stdio_client's task group is torn down across tasks.  The handler is
    # intentionally NOT restored: asyncgen finalization happens during
    # asyncio.run() shutdown (after run_agent returns), so restoring the
    # handler here would still let the noise through.  Since this runs
    # right before process exit, swallowing late exceptions is safe.
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(lambda _loop, _ctx: None)
    try:
        await cleanup_mcp_connections()
    except Exception:
        pass


def resolve_session_prefix(
    prefix: str, store: SessionStore, any_workspace: bool = False
) -> str:
    """Resolve a session-id prefix to a full session_id.

    Args:
        prefix: User-supplied prefix (e.g. first 6-12 chars of uuid).
        store: SessionStore for the current cwd.
        any_workspace: If True, also scan ~/.mini-agent/projects/*/sessions/.

    Raises:
        SessionNotFoundError: if no match, or prefix is ambiguous.
    """
    matches = [m for m in store.list_sessions() if m.session_id.startswith(prefix)]

    if any_workspace:
        projects_root = Path.home() / ".mini-agent" / "projects"
        if projects_root.exists():
            seen = {m.session_id for m in matches}
            for proj in projects_root.iterdir():
                sessions_dir_path = proj / "sessions"
                if not sessions_dir_path.is_dir():
                    continue
                for jsonl in sessions_dir_path.glob("*.jsonl"):
                    sid = jsonl.stem
                    if sid.startswith(prefix) and sid not in seen:
                        # Build a minimal meta via the store API
                        try:
                            other_store = SessionStore(
                                str(proj)
                            )  # cwd encoded; list_sessions reads files directly
                            for m in other_store.list_sessions():
                                if m.session_id == sid and sid not in seen:
                                    matches.append(m)
                                    seen.add(sid)
                        except Exception:
                            continue

    if not matches:
        raise SessionNotFoundError(f"no session matches prefix '{prefix}'")
    if len(matches) > 1:
        ids = sorted({m.session_id for m in matches})
        raise SessionNotFoundError(
            f"ambiguous prefix '{prefix}', matches: {ids}"
        )
    return matches[0].session_id


def maybe_prompt_in_progress(cwd: str) -> str | None:
    """If there's an in-progress session within 24h, prompt user.

    Returns session_id to resume, or None to start new.
    Returns ("fork", session_id) tuple if user chooses fork.
    """
    store = SessionStore(cwd)
    candidates = store.find_in_progress(max_age_hours=24)
    if not candidates:
        return None

    print(f"{Colors.BRIGHT_CYAN}💡 Detected in-progress session(s) within 24h:{Colors.RESET}")
    print()
    for i, meta in enumerate(candidates, 1):
        preview = (meta.preview or "")[:40]
        print(
            f"   [{i}] {meta.session_id[:8]}...  "
            f"{meta.message_count} msgs  {preview}"
        )
    print()
    print(f"   {Colors.DIM}Pick number to resume, 'n' for new, or 'f' to fork.{Colors.RESET}")

    while True:
        try:
            choice = input("   > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice in ("n", ""):
            return None
        if choice == "f":
            return ("fork", candidates[0].session_id)
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx].session_id
        print(f"   {Colors.DIM}invalid; enter number, 'n', or 'f'{Colors.RESET}")


def _cmd_sessions_list(workspace_dir):
    """List sessions in current workspace."""
    store = SessionStore(str(workspace_dir))
    metas = store.list_sessions()
    if not metas:
        print(f"{Colors.DIM}(no sessions){Colors.RESET}")
        return
    print(
        f"{Colors.BOLD}{'ID':<14} {'Started':<21} {'Status':<11} "
        f"{'Msgs':<5} Preview{Colors.RESET}"
    )
    for m in metas[:20]:
        started = (m.started_at or "")[:19]
        preview = (m.preview or "")[:40]
        if not m.ended:
            status_str = "● in-prog"
        else:
            status_str = "✓ ended"
        print(f"{m.session_id[:14]:<14} {started:<21} ", end="")
        print(f"{status_str:<11} ", end="")
        print(f"{m.message_count:<5} {preview}")


async def _cmd_resume_switch(agent, session_writer, workspace_dir, config, command_str):
    """Soft-switch: close current writer, resume target session.

    Mutates agent.session and agent.messages in-place.
    """
    parts = command_str.split(maxsplit=1)
    target = parts[1].strip().strip("\"'") if len(parts) > 1 else None

    store = SessionStore(str(workspace_dir))
    if target is None:
        metas = store.list_sessions()
        if not metas:
            print(f"{Colors.DIM}(no sessions to resume){Colors.RESET}")
            return
        print(f"{Colors.BRIGHT_CYAN}Recent sessions:{Colors.RESET}")
        for i, m in enumerate(metas[:10], 1):
            status = "in-prog" if not m.ended else "ended"
            preview = (m.preview or "")[:40]
            print(
                f"   [{i}] {m.session_id[:8]}...  ({status}, "
                f"{m.message_count} msgs)  {preview}"
            )
        try:
            choice = input(f"   {Colors.DIM}resume which? > {Colors.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if choice.isdigit():
            idx = int(choice) - 1
            slice_ = metas[:10]
            if 0 <= idx < len(slice_):
                target = slice_[idx].session_id
        else:
            target = choice

    if not target:
        return

    try:
        target = resolve_session_prefix(target, store)
    except Exception as exc:
        print(f"{Colors.RED}❌ {exc}{Colors.RESET}")
        return

    # Close current writer
    try:
        agent.session.close(end_reason="resume_switch")
    except Exception:
        pass

    # Load target messages
    reader = SessionReader(str(workspace_dir))
    try:
        new_msgs = reader.load_messages(target, mode="compacted")
    except Exception as exc:
        print(f"{Colors.RED}❌ Cannot load {target[:8]}: {exc}{Colors.RESET}")
        # Try to reopen current
        try:
            agent.session.open()
        except Exception:
            pass
        return

    # Construct new writer
    new_writer = SessionWriter(
        session_id=target,
        cwd=str(workspace_dir),
        workspace=str(workspace_dir),
        model=config.llm.model,
        cli_args={"resumed_from": session_writer.session_id},
    )
    try:
        new_writer.open()
    except SessionLockedError:
        print(
            f"{Colors.YELLOW}⚠️  Session {target[:8]} is live in another "
            f"process; not switched.{Colors.RESET}"
        )
        try:
            agent.session.open()
        except Exception:
            pass
        return

    # Swap state in-place
    agent.session = new_writer
    # Replace messages: keep system prompt (messages[0]), append loaded
    new_messages = [agent.messages[0]] + new_msgs
    agent.messages = new_messages
    # Reset _message_seqs with -1 sentinels for resumed messages
    agent._message_seqs = [-1] * len(new_msgs)
    print(
        f"{Colors.BRIGHT_GREEN}↪ Resumed session {target[:8]} "
        f"({len(new_msgs)} messages){Colors.RESET}"
    )


async def _cmd_fork_switch(agent, session_writer, workspace_dir, config, command_str):
    """Soft-switch: fork current (or specified) session.

    Mutates agent.session and agent.messages in-place.
    """
    import uuid as _uuid
    parts = command_str.split(maxsplit=1)
    target_arg = parts[1].strip().strip("\"'") if len(parts) > 1 else None
    store = SessionStore(str(workspace_dir))

    if target_arg is None:
        target = session_writer.session_id
    else:
        try:
            target = resolve_session_prefix(target_arg, store)
        except Exception as exc:
            print(f"{Colors.RED}❌ {exc}{Colors.RESET}")
            return

    new_id = _uuid.uuid4().hex[:12]
    try:
        store.fork_session(target, new_id)
    except Exception as exc:
        print(f"{Colors.RED}❌ Cannot fork {target[:8]}: {exc}{Colors.RESET}")
        return

    # Close current writer
    try:
        agent.session.close(end_reason="fork_switch")
    except Exception:
        pass

    # Load forked messages in raw mode
    reader = SessionReader(str(workspace_dir))
    try:
        new_msgs = reader.load_messages(new_id, mode="raw")
    except Exception as exc:
        print(f"{Colors.RED}❌ Cannot load fork: {exc}{Colors.RESET}")
        return

    new_writer = SessionWriter(
        session_id=new_id,
        cwd=str(workspace_dir),
        workspace=str(workspace_dir),
        model=config.llm.model,
        cli_args={"forked_from": target},
    )
    new_writer.open(forked_from=target)

    # Swap state
    agent.session = new_writer
    agent.messages = [agent.messages[0]] + new_msgs
    agent._message_seqs = [-1] * len(new_msgs)
    print(
        f"{Colors.BRIGHT_GREEN}⑂ Forked {target[:8]} → new session "
        f"{new_id[:8]}{Colors.RESET}"
    )


async def run_agent(workspace_dir: Path, task: str = None, args=None):
    """Run Agent in interactive or non-interactive mode.

    Args:
        workspace_dir: Workspace directory path
        task: If provided, execute this task and exit (non-interactive mode)
        args: argparse namespace with continue_latest/resume_id/fork_id/any_workspace.
              If None, defaults to "new session" behavior.
    """
    session_start = datetime.now()

    # 1. Load configuration from package directory
    config_path = Config.get_default_config_path()

    if not config_path.exists():
        print(f"{Colors.RED}❌ Configuration file not found{Colors.RESET}")
        print()
        print(f"{Colors.BRIGHT_CYAN}📦 Configuration Search Path:{Colors.RESET}")
        print(f"  {Colors.DIM}1) mini_agent/config/config.yaml{Colors.RESET} (development)")
        print(f"  {Colors.DIM}2) ~/.mini-agent/config/config.yaml{Colors.RESET} (user)")
        print(f"  {Colors.DIM}3) <package>/config/config.yaml{Colors.RESET} (installed)")
        print()
        print(f"{Colors.BRIGHT_YELLOW}🚀 Quick Setup (Recommended):{Colors.RESET}")
        print(
            f"  {Colors.BRIGHT_GREEN}curl -fsSL https://raw.githubusercontent.com/MiniMax-AI/Mini-Agent/main/scripts/setup-config.sh | bash{Colors.RESET}"
        )
        print()
        print(f"{Colors.DIM}  This will automatically:{Colors.RESET}")
        print(f"{Colors.DIM}    • Create ~/.mini-agent/config/{Colors.RESET}")
        print(f"{Colors.DIM}    • Download configuration files{Colors.RESET}")
        print(f"{Colors.DIM}    • Guide you to add your API Key{Colors.RESET}")
        print()
        print(f"{Colors.BRIGHT_YELLOW}📝 Manual Setup:{Colors.RESET}")
        user_config_dir = Path.home() / ".mini-agent" / "config"
        example_config = Config.get_package_dir() / "config" / "config-example.yaml"
        print(f"  {Colors.DIM}mkdir -p {user_config_dir}{Colors.RESET}")
        print(f"  {Colors.DIM}cp {example_config} {user_config_dir}/config.yaml{Colors.RESET}")
        print(f"  {Colors.DIM}# Then edit {user_config_dir}/config.yaml to add your API Key{Colors.RESET}")
        print()
        return

    try:
        config = Config.from_yaml(config_path)
    except FileNotFoundError:
        print(f"{Colors.RED}❌ Error: Configuration file not found: {config_path}{Colors.RESET}")
        return
    except ValueError as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}Please check the configuration file format{Colors.RESET}")
        return
    except Exception as e:
        print(f"{Colors.RED}❌ Error: Failed to load configuration file: {e}{Colors.RESET}")
        return

    # 2. Initialize LLM client
    from mini_agent.retry import RetryConfig as RetryConfigBase

    # Convert configuration format
    retry_config = RetryConfigBase(
        enabled=config.llm.retry.enabled,
        max_retries=config.llm.retry.max_retries,
        initial_delay=config.llm.retry.initial_delay,
        max_delay=config.llm.retry.max_delay,
        exponential_base=config.llm.retry.exponential_base,
        retryable_exceptions=(Exception,),
    )

    # Create retry callback function to display retry information in terminal
    def on_retry(exception: Exception, attempt: int):
        """Retry callback function to display retry information"""
        print(f"\n{Colors.BRIGHT_YELLOW}⚠️  LLM call failed (attempt {attempt}): {str(exception)}{Colors.RESET}")
        next_delay = retry_config.calculate_delay(attempt - 1)
        print(f"{Colors.DIM}   Retrying in {next_delay:.1f}s (attempt {attempt + 1})...{Colors.RESET}")

    # Convert provider string to LLMProvider enum
    provider = LLMProvider.ANTHROPIC if config.llm.provider.lower() == "anthropic" else LLMProvider.OPENAI

    llm_client = LLMClient(
        api_key=config.llm.api_key,
        provider=provider,
        api_base=config.llm.api_base,
        model=config.llm.model,
        retry_config=retry_config if config.llm.retry.enabled else None,
    )

    # Set retry callback
    if config.llm.retry.enabled:
        llm_client.retry_callback = on_retry
        print(f"{Colors.GREEN}✅ LLM retry mechanism enabled (max {config.llm.retry.max_retries} retries){Colors.RESET}")

    # 3. Initialize base tools (independent of workspace)
    tools, skill_loader = await initialize_base_tools(config)

    # 3.5. Create security middleware
    security = SecurityMiddleware(config.security, str(workspace_dir), interactive=(task is None))

    # 4. Add workspace-dependent tools
    add_workspace_tools(tools, config, workspace_dir, security)

    # 4.5 Initialize persistent memory system
    memory_context = ""
    if config.tools.enable_memory:
        memory_manager = MemoryManager(workspace_dir)
        memory_context = memory_manager.initialize_memory()
        print(f"{Colors.GREEN}✅ Loaded memory system (dir: {memory_manager.memory_dir}){Colors.RESET}")

    # 4.6 Legacy file detection (warn about deprecated files)
    _warn_legacy_files(workspace_dir)

    # 5. Load System Prompt (with priority search)
    system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
    if system_prompt_path and system_prompt_path.exists():
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
        print(f"{Colors.GREEN}✅ Loaded system prompt (from: {system_prompt_path}){Colors.RESET}")
    else:
        system_prompt = "You are Mini-Agent, an intelligent assistant powered by MiniMax M2.5 that can help users complete various tasks."
        print(f"{Colors.YELLOW}⚠️  System prompt not found, using default{Colors.RESET}")

    # 6. Inject Skills Metadata into System Prompt (Progressive Disclosure - Level 1)
    if skill_loader:
        skills_metadata = skill_loader.get_skills_metadata_prompt()
        if skills_metadata:
            # Replace placeholder with actual metadata
            system_prompt = system_prompt.replace("{SKILLS_METADATA}", skills_metadata)
            print(f"{Colors.GREEN}✅ Injected {len(skill_loader.loaded_skills)} skills metadata into system prompt{Colors.RESET}")
        else:
            # Remove placeholder if no skills
            system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
    else:
        # Remove placeholder if skills not enabled
        system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")

    # 7. Resolve session resume/fork/new
    import uuid as _uuid

    cwd_str = str(workspace_dir)
    reader = SessionReader(cwd_str)
    store = SessionStore(cwd_str)

    resume_id = None
    fork_id = None

    if args and getattr(args, "continue_latest", False):
        latest = store.find_latest()
        if latest is None:
            print(f"{Colors.RED}❌ No session to continue in current cwd.{Colors.RESET}")
            return
        resume_id = latest.session_id
    elif args and getattr(args, "resume_id", None):
        try:
            resume_id = resolve_session_prefix(
                args.resume_id, store, getattr(args, "any_workspace", False)
            )
        except SessionNotFoundError as exc:
            print(f"{Colors.RED}❌ {exc}{Colors.RESET}")
            return
    elif args and getattr(args, "fork_id", None):
        try:
            fork_id = resolve_session_prefix(args.fork_id, store, any_workspace=False)
        except SessionNotFoundError as exc:
            print(f"{Colors.RED}❌ {exc}{Colors.RESET}")
            return

    # NEW: if no explicit resume/fork flag AND interactive mode, prompt for in-progress
    if not resume_id and not fork_id and not task:
        prompted = maybe_prompt_in_progress(cwd_str)
        if isinstance(prompted, tuple) and prompted[0] == "fork":
            fork_id = prompted[1]
        elif isinstance(prompted, str):
            resume_id = prompted

    # Branch: new / resume / fork
    loaded_messages: list = []  # messages to seed Agent (besides system prompt)
    if resume_id:
        try:
            loaded_messages = reader.load_messages(resume_id, mode="compacted")
            session_writer = SessionWriter(
                session_id=resume_id,
                cwd=cwd_str,
                workspace=str(workspace_dir),
                model=config.llm.model,
                cli_args={},
            )
            session_writer.open()  # acquires lock; does NOT write session_start
        except SessionLockedError:
            print(
                f"{Colors.RED}❌ Session {resume_id} is locked by another process. "
                f"Use --fork instead.{Colors.RESET}"
            )
            return
    elif fork_id:
        new_id = _uuid.uuid4().hex[:12]
        store.fork_session(fork_id, new_id)
        loaded_messages = reader.load_messages(fork_id, mode="raw")
        session_writer = SessionWriter(
            session_id=new_id,
            cwd=cwd_str,
            workspace=str(workspace_dir),
            model=config.llm.model,
            cli_args={},
        )
        session_writer.open(forked_from=fork_id)
    else:
        new_id = _uuid.uuid4().hex[:12]
        session_writer = SessionWriter(
            session_id=new_id,
            cwd=cwd_str,
            workspace=str(workspace_dir),
            model=config.llm.model,
            cli_args={},
        )
        session_writer.open()

    # Model mismatch warning
    if resume_id or fork_id:
        source_sid = resume_id or fork_id
        source_meta = store._derive_meta(store.session_path(source_sid))
        if (
            source_meta
            and source_meta.model
            and source_meta.model != config.llm.model
        ):
            print(
                f"{Colors.YELLOW}⚠️  Session was created with model "
                f"{source_meta.model}, current is {config.llm.model}.{Colors.RESET}"
            )
            print(
                f"{Colors.DIM}   Historical summaries generated under "
                f"{source_meta.model} may be suboptimal.{Colors.RESET}"
            )

    # Construct Agent
    agent = Agent(
        llm_client=llm_client,
        system_prompt=system_prompt,
        tools=tools,
        max_steps=config.agent.max_steps,
        workspace_dir=str(workspace_dir),
        memory_context=memory_context,
        session_writer=session_writer,
    )

    # Seed resumed/forked messages AFTER Agent construction.
    # Agent.messages starts as [system]; append loaded messages and sync _message_seqs.
    # NOTE: persisted messages already exist in JSONL — we must NOT re-append them
    # to the session (would create duplicates). Just inline into agent.messages.
    for msg in loaded_messages:
        agent.messages.append(msg)
        # _message_seqs tracks seq of persisted events. These messages are already
        # in the JSONL; their seqs are unknown without re-reading. Use -1 sentinel
        # to indicate "already persisted, do not re-write on summary".
        agent._message_seqs.append(-1)

    # 7.5. Session banner
    short_id = session_writer.session_id[:8]
    print(
        f"{Colors.DIM}🆔 Session {session_writer.session_id}  "
        f"(resume: mini-agent --resume {short_id}  |  "
        f"fork: mini-agent --fork {short_id}){Colors.RESET}"
    )

    # 8. Display welcome information
    if not task:
        print_banner()
        print_session_info(agent, workspace_dir, config.llm.model)

    # 8.5 Non-interactive mode: execute task and exit
    if task:
        print(f"\n{Colors.BRIGHT_BLUE}Agent{Colors.RESET} {Colors.DIM}›{Colors.RESET} {Colors.DIM}Executing task...{Colors.RESET}\n")
        agent.add_user_message(task)
        try:
            await agent.run()
        except Exception as e:
            print(f"\n{Colors.RED}❌ Error: {e}{Colors.RESET}")
        finally:
            print_stats(agent, session_start)
            session_writer.close(end_reason="task")

        # Cleanup MCP connections
        await _quiet_cleanup()
        return

    # 9. Setup prompt_toolkit session
    # Command completer
    command_completer = WordCompleter(
        ["/help", "/clear", "/history", "/stats", "/init", "/bg",
         "/sessions", "/resume", "/fork", "/session-id",
         "/exit", "/quit", "/q"],
        ignore_case=True,
        sentence=True,
    )

    # Custom style for prompt
    prompt_style = Style.from_dict(
        {
            "prompt": "#00ff00 bold",  # Green and bold
            "separator": "#666666",  # Gray
        }
    )

    # Custom key bindings
    kb = KeyBindings()

    @kb.add("c-u")  # Ctrl+U: Clear current line
    def _(event):
        """Clear the current input line"""
        event.current_buffer.reset()

    @kb.add("c-l")  # Ctrl+L: Clear screen (optional bonus)
    def _(event):
        """Clear the screen"""
        event.app.renderer.clear()

    @kb.add("c-j")  # Ctrl+J (对应 Ctrl+Enter)
    def _(event):
        """Insert a newline"""
        event.current_buffer.insert_text("\n")

    # Create prompt session with history and auto-suggest
    # Use FileHistory for persistent history across sessions (stored in user's home directory)
    history_file = Path.home() / ".mini-agent" / ".history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=command_completer,
        style=prompt_style,
        key_bindings=kb,
    )

    # 10. Interactive mode with signal handlers
    import signal

    sig_state = {"requested": None}

    def _sig_handler(signum, _frame):
        if sig_state["requested"] is not None:
            # Second signal: exit immediately
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            raise KeyboardInterrupt
        sig_state["requested"] = (
            "interrupt" if signum == signal.SIGINT else "sigterm"
        )

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, OSError):
        # Not in main thread (e.g. test env) — skip
        pass

    try:
        await run_repl(agent, session_writer, session, workspace_dir, session_start, config)
    except KeyboardInterrupt:
        pass
    finally:
        end_reason = sig_state["requested"] or "exit"
        try:
            session_writer.close(end_reason=end_reason)
        except Exception:
            pass

    return


async def run_repl(
    agent,
    session_writer,
    session,
    workspace_dir: Path,
    session_start,
    config=None,
):
    """Interactive REPL loop extracted from run_agent for testability.

    Args:
        agent: Agent instance with messages already seeded.
        session_writer: Opened SessionWriter (closed by caller's finally block).
        session: prompt_toolkit PromptSession for input.
        workspace_dir: Workspace directory path.
        session_start: datetime when the session started.
        config: Config object (needed by /resume and /fork slash commands).
    """
    # Interactive loop
    while True:
        try:
            # Get user input using prompt_toolkit
            # 动态 prompt：显示后台进程数量
            running_count = BackgroundShellManager.get_running_count()
            prompt_parts = [("class:prompt", "You")]
            if running_count > 0:
                prompt_parts.append(("", f" [⚙️ {running_count} bg]"))
            prompt_parts.append(("", " › "))

            user_input = await session.prompt_async(
                prompt_parts,
                multiline=False,
                enable_history_search=True,
            )
            user_input = user_input.strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                command = user_input.lower()

                if command in ["/exit", "/quit", "/q"]:
                    print(f"\n{Colors.BRIGHT_YELLOW}👋 Goodbye! Thanks for using Mini Agent{Colors.RESET}\n")
                    print_stats(agent, session_start)
                    break

                elif command == "/help":
                    print_help()
                    continue

                elif command == "/clear":
                    # Clear message history but keep system prompt
                    old_count = len(agent.messages)
                    agent.messages = [agent.messages[0]]  # Keep only system message
                    print(f"{Colors.GREEN}✅ Cleared {old_count - 1} messages, starting new session{Colors.RESET}\n")
                    continue

                elif command == "/history":
                    print(f"\n{Colors.BRIGHT_CYAN}Current session message count: {len(agent.messages)}{Colors.RESET}\n")
                    continue

                elif command == "/stats":
                    print_stats(agent, session_start)
                    continue

                elif command == "/bg":
                    _print_background_status()
                    continue

                elif command == "/sessions":
                    _cmd_sessions_list(workspace_dir)
                    print()
                    continue

                elif command == "/session-id":
                    print(
                        f"{Colors.BRIGHT_CYAN}Current session:{Colors.RESET} "
                        f"{session_writer.session_id}"
                    )
                    print(
                        f"{Colors.DIM}   Resume: mini-agent --resume "
                        f"{session_writer.session_id[:8]}{Colors.RESET}"
                    )
                    print()
                    continue

                elif command == "/resume" or command.startswith("/resume "):
                    if config is None:
                        print(
                            f"{Colors.RED}❌ /resume requires config "
                            f"(unavailable in this context){Colors.RESET}"
                        )
                        continue
                    await _cmd_resume_switch(
                        agent, session_writer, workspace_dir, config, user_input
                    )
                    # Update local session_writer reference so subsequent
                    # /session-id prints reflect the new session.
                    session_writer = agent.session
                    print()
                    continue

                elif command == "/fork" or command.startswith("/fork "):
                    if config is None:
                        print(
                            f"{Colors.RED}❌ /fork requires config "
                            f"(unavailable in this context){Colors.RESET}"
                        )
                        continue
                    await _cmd_fork_switch(
                        agent, session_writer, workspace_dir, config, user_input
                    )
                    session_writer = agent.session
                    print()
                    continue

                elif command == "/init":
                    # Inject the init prompt as a normal user message and fall
                    # through to the regular agent execution flow below (which
                    # provides Esc cancellation, ConfirmSelector integration,
                    # background-process status printing, and error handling).
                    # No `continue` here — we want the fall-through.
                    user_input = INIT_PROMPT_TEMPLATE.format(workspace=str(workspace_dir))

                else:
                    print(f"{Colors.RED}❌ Unknown command: {user_input}{Colors.RESET}")
                    print(f"{Colors.DIM}Type /help to see available commands{Colors.RESET}\n")
                    continue

            # Normal conversation - exit check
            if user_input.lower() in ["exit", "quit", "q"]:
                print(f"\n{Colors.BRIGHT_YELLOW}👋 Goodbye! Thanks for using Mini Agent{Colors.RESET}\n")
                print_stats(agent, session_start)
                break

            # Run Agent with Esc cancellation support
            print(
                f"\n{Colors.BRIGHT_BLUE}Agent{Colors.RESET} {Colors.DIM}›{Colors.RESET} {Colors.DIM}Thinking... (Esc to cancel){Colors.RESET}\n"
            )
            agent.add_user_message(user_input)

            # Create cancellation event
            cancel_event = asyncio.Event()
            agent.cancel_event = cancel_event

            # Esc key listener thread
            esc_listener_stop = threading.Event()
            esc_cancelled = [False]  # Mutable container for thread access

            def esc_key_listener():
                """Listen for Esc key in a separate thread."""
                if platform.system() == "Windows":
                    try:
                        import msvcrt

                        while not esc_listener_stop.is_set():
                            if msvcrt.kbhit():
                                char = msvcrt.getch()
                                if char == b"\x1b":  # Esc
                                    print(f"\n{Colors.BRIGHT_YELLOW}⏹️  Esc pressed, cancelling...{Colors.RESET}")
                                    esc_cancelled[0] = True
                                    cancel_event.set()
                                    break
                            esc_listener_stop.wait(0.05)
                    except Exception:
                        pass
                    return

                # Unix/macOS
                try:
                    import select
                    import termios
                    import tty

                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)

                    try:
                        tty.setcbreak(fd)
                        while not esc_listener_stop.is_set():
                            rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if rlist:
                                char = sys.stdin.read(1)
                                if char == "\x1b":  # Esc
                                    print(f"\n{Colors.BRIGHT_YELLOW}⏹️  Esc pressed, cancelling...{Colors.RESET}")
                                    esc_cancelled[0] = True
                                    cancel_event.set()
                                    break
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass

            # Start Esc listener thread
            esc_thread = threading.Thread(target=esc_key_listener, daemon=True)
            esc_thread.start()

            # Run agent with periodic cancellation check
            try:
                agent_task = asyncio.create_task(agent.run())

                # Poll for cancellation and confirmation while agent runs
                while not agent_task.done():
                    if esc_cancelled[0]:
                        cancel_event.set()

                    # Check if agent is paused for confirmation
                    if agent.confirmation_request is not None:
                        # Stop Esc listener to free stdin for prompt_toolkit
                        esc_listener_stop.set()
                        esc_thread.join(timeout=0.3)

                        req = agent.confirmation_request
                        selector = ConfirmSelector(
                            command=req.command, reason=req.reason
                        )
                        result = await selector.select()

                        if result.action == "yes":
                            agent.resolve_confirmation("yes")
                        elif result.action == "always":
                            agent.resolve_confirmation("always")
                        elif result.action == "feedback":
                            agent.resolve_confirmation(None, feedback=result.feedback)
                        else:
                            agent.resolve_confirmation(None)

                        # Restart Esc listener after confirmation
                        esc_listener_stop = threading.Event()
                        esc_thread = threading.Thread(target=esc_key_listener, daemon=True)
                        esc_thread.start()

                    await asyncio.sleep(0.1)

                # Get result
                _ = agent_task.result()

            except asyncio.CancelledError:
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  Agent execution cancelled{Colors.RESET}")
            finally:
                agent.cancel_event = None
                esc_listener_stop.set()
                esc_thread.join(timeout=0.2)

            # 如果有正在运行的后台进程，显示状态摘要
            running_summaries = [
                s for s in BackgroundShellManager.get_summary() if s["status"] == "running"
            ]
            if running_summaries:
                items = []
                for s in running_summaries:
                    time_str = _format_elapsed(s["elapsed"])
                    cmd = s["command"][:30] + "..." if len(s["command"]) > 30 else s["command"]
                    items.append(f"{s['bash_id']} ({cmd}, {time_str})")
                suffix = "es" if len(running_summaries) > 1 else ""
                print(
                    f"\n{Colors.BRIGHT_YELLOW}⚙️  {len(running_summaries)} 个后台进程"
                    f"运行中: {', '.join(items)}{Colors.RESET}"
                )

            # Visual separation
            print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.BRIGHT_YELLOW}👋 Interrupt signal detected, exiting...{Colors.RESET}\n")
            print_stats(agent, session_start)
            break

        except Exception as e:
            print(f"\n{Colors.RED}❌ Error: {e}{Colors.RESET}")
            print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}\n")

    # Cleanup MCP connections
    await _quiet_cleanup()


def main():
    """Main entry point for CLI"""
    # Parse command line arguments
    args = parse_args()

    # Handle session subcommand (cat/list/tail/rm)
    if args.command == "session":
        from mini_agent.session.cli import handle_session_command

        sys.exit(handle_session_command(args, str(Path.cwd())))

    # Handle migrate-orphans subcommand
    if args.command == "migrate-orphans":
        import os as _os

        from mini_agent.session.migrate import migrate_all

        _os.environ["MINI_AGENT_FORCE_MIGRATE"] = "1"
        migrated, orphans = migrate_all(cwd=str(Path.cwd()))
        print(f"Migration complete: {migrated} moved, {orphans} orphans remaining")
        sys.exit(0)

    # Determine workspace directory
    # Expand ~ to user home directory for portability
    if args.workspace:
        workspace_dir = Path(args.workspace).expanduser().absolute()
    else:
        # Use current working directory
        workspace_dir = Path.cwd()

    # Ensure workspace directory exists
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Run migration from legacy <name>_<hash8> dirs to encoded-cwd layout.
    # Idempotent via marker file; runs once per home directory.
    from mini_agent.session.migrate import migrate_all

    migrate_all(cwd=str(workspace_dir))

    # Run the agent (config always loaded from package directory)
    asyncio.run(run_agent(workspace_dir, task=args.task, args=args))


if __name__ == "__main__":
    main()
