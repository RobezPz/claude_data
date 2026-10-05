# AGENTS.md

## Project purpose
Archives Claude.ai conversations for offline viewing: log in with credentials from `.env`, scroll through "Recent" to collect all conversations, save rendered output to `archive/`. `README.md` contains the original prompt/spec — treat it as the requirements document.

## Commands
- `uv sync` — create/sync `.venv` (uv only; never pip)
- `uv run claude-data` — run the CLI (console script -> `main()` in `src/claude_data/__init__.py`)
- `uv add <pkg>` — add dependencies
- Lint: `uvx ruff check` ; Typecheck: `uvx pyrefly check` — neither is a project dependency yet, so invoke via `uvx`; run lint before typecheck

## Toolchain (mandated by README)
- Python >= 3.13, src layout, `uv_build` backend
- uv for dependencies, ruff for linting, pyrefly for typechecking

## Secrets and generated data
- `.env` holds the Claude.ai login email (`CLAUDE_USERNAME` only — email-code login, no password); it is gitignored — never commit, print, or log its contents
- `archive/` is generated output (gitignored): regenerable, safe to delete, never commit

## Current state
Design spec committed (`docs/superpowers/specs/2026-10-04-claude-conversation-archiver-design.md`); no code, tests, or CI yet. Single package `claude_data`.
