"""Memory Manager - Persistent cross-session memory for Mini-Agent.

Manages per-project memory files stored at ~/.mini-agent/projects/<path>/memory/.
Inspired by Claude Code's memory system.
"""

import subprocess
from pathlib import Path


class MemoryManager:
    """Manages persistent memory for the Mini-Agent.

    Directory structure:
        ~/.mini-agent/projects/<escaped-project-path>/memory/
            MEMORY.md         # Main memory index (auto-loaded, first 200 lines)
            <topic>.md        # Topic-specific files (on-demand via file tools)

    Also loads instructions from (in priority order):
        ~/.mini-agent/AGENTS.md              # Global user preferences (all projects)
        <workspace>/AGENTS.md                # Project-specific instructions
        <memory_dir>/MEMORY.md               # Auto-saved memory index
    """

    MAX_INDEX_LINES = 200
    PROJECT_INSTRUCTION_FILENAME = "AGENTS.md"
    GLOBAL_INSTRUCTION_FILE = Path.home() / ".mini-agent" / "AGENTS.md"

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).absolute()
        self.project_root = self._find_project_root()
        self.memory_dir = self._resolve_memory_dir()

    def _find_project_root(self) -> Path:
        """Find the project root by looking for a git repo.

        Walks up from workspace_dir to find a .git directory.
        Falls back to workspace_dir if not in a git repo.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return self.workspace_dir

    def _resolve_memory_dir(self) -> Path:
        """Resolve the per-project memory directory.

        Uses encoded-cwd path: ~/.mini-agent/projects/<encoded-cwd>/memory/
        Migration from the legacy <name>_<hash8> format is handled by
        mini_agent.session.migrate on first startup of the new version.
        """
        from ..paths import memory_dir
        return memory_dir(str(self.project_root))

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
        """Load AGENTS.md from project root (git repo root or workspace_dir).

        Returns None if file doesn't exist.
        """
        instruction_file = self.project_root / self.PROJECT_INSTRUCTION_FILENAME
        if not instruction_file.exists():
            return None
        return instruction_file.read_text(encoding="utf-8")

    def load_global_instructions(self) -> str | None:
        """Load AGENTS.md from ~/.mini-agent/ (global user preferences).

        Returns None if file doesn't exist.
        """
        if not self.GLOBAL_INSTRUCTION_FILE.exists():
            return None
        return self.GLOBAL_INSTRUCTION_FILE.read_text(encoding="utf-8")

    def build_memory_context(self) -> str:
        """Build the full memory context string for injection into system prompt.

        Combines global instructions, project instructions, and memory index.
        Returns empty string if none exist.
        """
        parts = []

        global_instructions = self.load_global_instructions()
        if global_instructions:
            parts.append(
                "## Global User Preferences (from ~/.mini-agent/AGENTS.md)\n\n"
                f"{global_instructions}"
            )

        instructions = self.load_project_instructions()
        if instructions:
            parts.append(
                "## Project Instructions (from AGENTS.md)\n\n"
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
