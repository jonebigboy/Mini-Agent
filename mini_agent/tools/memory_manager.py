"""Memory Manager - Persistent cross-session memory for Mini-Agent.

Manages per-project memory files stored at ~/.mini-agent/projects/<path>/memory/.
Inspired by Claude Code's memory system.
"""

import hashlib
from pathlib import Path


class MemoryManager:
    """Manages persistent memory for the Mini-Agent.

    Directory structure:
        ~/.mini-agent/projects/<escaped-project-path>/memory/
            MEMORY.md         # Main memory index (auto-loaded, first 200 lines)
            <topic>.md        # Topic-specific files (on-demand via file tools)

    Also loads project instructions from:
        <workspace>/MINI_AGENT.md
    """

    MAX_INDEX_LINES = 200
    PROJECT_INSTRUCTION_FILENAME = "MINI_AGENT.md"

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).absolute()
        self.memory_dir = self._resolve_memory_dir()

    def _resolve_memory_dir(self) -> Path:
        """Resolve the per-project memory directory.

        Path: ~/.mini-agent/projects/<name>_<hash8>/memory/
        Name is derived from the last path component for readability.
        Hash (first 8 chars of SHA256) prevents collisions between paths
        like /Users/jone/my_project and /Users/jone_my/project.
        Example: /Users/jone/Mini-Agent -> Mini-Agent_a1b2c3d4/memory/
        """
        path_str = str(self.workspace_dir)
        name = self.workspace_dir.name or "root"
        hash8 = hashlib.sha256(path_str.encode()).hexdigest()[:8]
        return Path.home() / ".mini-agent" / "projects" / f"{name}_{hash8}" / "memory"

    def ensure_memory_dir(self) -> Path:
        """Create memory directory if it doesn't exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        return self.memory_dir

    def load_memory_index(self) -> str | None:
        """Load the first MAX_INDEX_LINES lines of MEMORY.md.

        Returns None if file doesn't exist.
        """
        memory_file = self.memory_dir / "MEMORY.md"
        if not memory_file.exists():
            return None

        text = memory_file.read_text(encoding="utf-8")
        lines = text.splitlines()

        if len(lines) <= self.MAX_INDEX_LINES:
            return text

        truncated = "\n".join(lines[: self.MAX_INDEX_LINES])
        truncated += (
            f"\n\n... [Memory index truncated: "
            f"showing {self.MAX_INDEX_LINES} of {len(lines)} lines]"
        )
        return truncated

    def load_project_instructions(self) -> str | None:
        """Load MINI_AGENT.md from workspace root.

        Returns None if file doesn't exist.
        """
        instruction_file = self.workspace_dir / self.PROJECT_INSTRUCTION_FILENAME
        if not instruction_file.exists():
            return None
        return instruction_file.read_text(encoding="utf-8")

    def build_memory_context(self) -> str:
        """Build the full memory context string for injection into system prompt.

        Combines project instructions (MINI_AGENT.md) and memory index (MEMORY.md).
        Returns empty string if neither exists.
        """
        parts = []

        instructions = self.load_project_instructions()
        if instructions:
            parts.append(
                "## Project Instructions (from MINI_AGENT.md)\n\n"
                f"{instructions}"
            )

        memory_index = self.load_memory_index()
        if memory_index:
            parts.append(
                "## Persistent Memory Index (from MEMORY.md)\n\n"
                f"{memory_index}"
            )

        if not parts:
            return ""

        header = (
            "# Cross-Session Memory\n\n"
            "The following content was automatically loaded from persistent memory. "
            "This information carries across sessions.\n\n"
        )

        footer = (
            f"\n\nMemory directory: `{self.memory_dir}`\n"
            "Use Read/Write/Edit tools to manage memory files in this directory. "
            "Keep memory concise and well-organized."
        )

        return header + "\n---\n\n".join(parts) + footer

    def initialize_memory(self) -> str:
        """Initialize memory directory and return the memory context.

        Called once at session start. Creates the memory directory if needed.
        Returns the memory context string (may be empty for first session).
        """
        self.ensure_memory_dir()
        return self.build_memory_context()
