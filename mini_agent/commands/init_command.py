"""Prompt template for the `/init` slash command.

When the user types `/init`, cli.py substitutes this template with the
current workspace path and injects it as a normal user message. The agent
then uses its existing tools (Read/Bash/Write) to explore the project and
produce `<workspace>/AGENTS.md`.

Design references:
- Spec: docs/superpowers/specs/2026-06-15-init-command-design.md
- Inspired by Claude Code's /init command.
"""

INIT_PROMPT_TEMPLATE = """\
Please analyze the current workspace ({workspace}) and generate an \
AGENTS.md file at the workspace root to give future Agent sessions \
project-specific context.

Suggested exploration steps (adapt as needed):
1. Run `ls -la` to see the top-level structure.
2. Read README.md if it exists.
3. Read project manifest files: pyproject.toml / package.json / Cargo.toml / \
go.mod / pom.xml / build.gradle (whichever exists).
4. Read other AI assistant rule files if they exist: .cursorrules, \
.github/copilot-instructions.md, existing AGENTS.md (as reference only, \
do not copy).
5. Run `git log --oneline -10` and `git status` for project context.
6. Read entry-point source files as needed (e.g., src/main.py, lib/index.js).

Then use the Write tool to create {workspace}/AGENTS.md.

Output requirements:
- Language: match the primary language of README.md, or code comments if no README.
- Length: 60-300 lines total.
- Include these sections (order may vary):
  - Project Overview (1-3 sentences)
  - Development Commands (build, test, run, lint)
  - Architecture (core components, directory layout)
  - Key Conventions (code style, naming, commit rules)
  - Testing Strategy (how to run tests, test organization)
- DO NOT include: API documentation, standard language conventions, \
information easily inferred from code, frequently-changing info (e.g., \
version numbers).
- ONLY include: things a future Agent cannot infer directly from the code.

After writing, briefly report the file path and line count.
"""
