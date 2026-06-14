"""Agent run logger"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import Message, ToolCall

LOG_TTL_DAYS = 30


class AgentLogger:
    """Agent run logger

    Responsible for recording the complete interaction process of each agent run, including:
    - LLM requests and responses
    - Tool calls and results
    """

    def __init__(self):
        """Initialize logger.

        Logs are stored in ~/.mini-agent/log/ directory. A new log file is
        created immediately for the session (one file per CLI session).
        """
        self.log_dir = Path.home() / ".mini-agent" / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = None
        self.log_index = 0
        self.run_count = 0
        self.start_new_session()

    def start_new_session(self):
        """Start a new session: create a new log file and reset counters.

        Called automatically from __init__. One AgentLogger instance maps to
        one CLI session, which maps to one log file.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"agent_run_{timestamp}.log"
        self.log_file = self.log_dir / log_filename
        self.log_index = 0
        self.run_count = 0

        # Write log header
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Agent Run Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

        # Prune logs older than the TTL.
        removed = self._cleanup_old_logs()
        if removed:
            print(f"🗑 Cleaned {removed} log file(s) older than {LOG_TTL_DAYS} days")

    def _cleanup_old_logs(self, max_age_days: int = LOG_TTL_DAYS) -> int:
        """Delete log files older than max_age_days.

        Returns the number of files deleted. Errors on individual files are
        swallowed (printed as warnings) so a single bad file cannot abort
        session startup. The file just created by start_new_session is never
        deleted because its mtime is current.
        """
        now = datetime.now().timestamp()
        cutoff = now - (max_age_days * 24 * 3600)
        deleted = 0

        for entry in self.log_dir.glob("*.log"):
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    entry.unlink()
                    deleted += 1
                except OSError as exc:
                    # Don't abort startup over one unremovable file.
                    print(f"[logger] could not delete {entry}: {exc}")

        return deleted

    def start_new_run_section(self):
        """Mark the start of a new run within the current session.

        Writes a separator header before each run except the first (which is
        already covered by the session file header). Resets log_index so the
        [N] entry numbers are scoped per-run.
        """
        self.run_count += 1
        self.log_index = 0

        if self.run_count == 1:
            # First run: file header from start_new_session already covers it.
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"--- Run #{self.run_count} started at {timestamp} "
        num_dashes = max(0, 80 - len(prefix))
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(prefix + "-" * num_dashes + "\n")
            f.write("=" * 80 + "\n")

    def log_request(self, messages: list[Message], tools: list[Any] | None = None):
        """Log LLM request

        Args:
            messages: Message list
            tools: Tool list (optional)
        """
        self.log_index += 1

        # Build complete request data structure
        request_data = {
            "messages": [],
            "tools": [],
        }

        # Convert messages to JSON serializable format
        for msg in messages:
            msg_dict = {
                "role": msg.role,
                "content": msg.content,
            }
            if msg.thinking:
                msg_dict["thinking"] = msg.thinking
            if msg.tool_calls:
                msg_dict["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            if msg.tool_call_id:
                msg_dict["tool_call_id"] = msg.tool_call_id
            if msg.name:
                msg_dict["name"] = msg.name

            request_data["messages"].append(msg_dict)

        # Only record tool names
        if tools:
            request_data["tools"] = [tool.name for tool in tools]

        # Format as JSON
        content = "LLM Request:\n\n"
        content += json.dumps(request_data, indent=2, ensure_ascii=False)

        self._write_log("REQUEST", content)

    def log_response(
        self,
        content: str,
        thinking: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str | None = None,
    ):
        """Log LLM response

        Args:
            content: Response content
            thinking: Thinking content (optional)
            tool_calls: Tool call list (optional)
            finish_reason: Finish reason (optional)
        """
        self.log_index += 1

        # Build complete response data structure
        response_data = {
            "content": content,
        }

        if thinking:
            response_data["thinking"] = thinking

        if tool_calls:
            response_data["tool_calls"] = [tc.model_dump() for tc in tool_calls]

        if finish_reason:
            response_data["finish_reason"] = finish_reason

        # Format as JSON
        log_content = "LLM Response:\n\n"
        log_content += json.dumps(response_data, indent=2, ensure_ascii=False)

        self._write_log("RESPONSE", log_content)

    def log_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_success: bool,
        result_content: str | None = None,
        result_error: str | None = None,
    ):
        """Log tool execution result

        Args:
            tool_name: Tool name
            arguments: Tool arguments
            result_success: Whether successful
            result_content: Result content (on success)
            result_error: Error message (on failure)
        """
        self.log_index += 1

        # Build complete tool execution result data structure
        tool_result_data = {
            "tool_name": tool_name,
            "arguments": arguments,
            "success": result_success,
        }

        if result_success:
            tool_result_data["result"] = result_content
        else:
            tool_result_data["error"] = result_error

        # Format as JSON
        content = "Tool Execution:\n\n"
        content += json.dumps(tool_result_data, indent=2, ensure_ascii=False)

        self._write_log("TOOL_RESULT", content)

    def _write_log(self, log_type: str, content: str):
        """Write log entry

        Args:
            log_type: Log type (REQUEST, RESPONSE, TOOL_RESULT)
            content: Log content
        """
        if self.log_file is None:
            return

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n" + "-" * 80 + "\n")
            f.write(f"[{self.log_index}] {log_type}\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
            f.write("-" * 80 + "\n")
            f.write(content + "\n")

    def get_log_file_path(self) -> Path:
        """Get current log file path"""
        return self.log_file
