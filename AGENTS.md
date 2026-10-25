# AGENTS.md

## Project purpose

Archives Claude.ai conversations for offline viewing: log in with credentials from `.env`, scroll through "Recent" to collect all conversations, save rendered output to `archive/`. `README.md` contains the original prompt/spec — treat it as the requirements document.

## Commands

- `uv sync` — create/sync `.venv` (uv only; never pip)
- `uv run claude-data` — run the CLI (console script -> `main()` in `src/claude_data/__init__.py`); flags: `--login`, `--headed` (compatibility no-op), `--refresh [UUID]` (bare = all)
- `uv run pytest` — run tests
- `uv add <pkg>` — add dependencies
- Lint: `uvx ruff check` ; Typecheck: `uvx pyrefly check` — neither is a project dependency yet, so invoke via `uvx`; run lint before typecheck

## Toolchain (mandated by README)

- Python >= 3.13, src layout, `uv_build` backend
- uv for dependencies, ruff for linting, pyrefly for typechecking

## Secrets and generated data

- `.env` holds the Claude.ai login email (`CLAUDE_USERNAME` only — email-code login, no password); it is gitignored — never commit, print, or log its contents
- `archive/` is generated output (gitignored): regenerable, safe to delete, never commit
- `~/.cache/claude_data/profile/` is the persistent Playwright profile (holds the session and Cloudflare clearance)
- `.superpowers/sdd/` holds execution ledger and discovery probes (gitignored)

## Runtime facts discovered during implementation

- The browser must run **headed**: Cloudflare challenges every headless build (default and `channel="chromium"`), while headed passes. Launch args `ignore_default_args=["--enable-automation"]` + `--disable-blink-features=AutomationControlled` are required, and a challenge page is detected by its "Just a moment..." title (the URL alone is not a reliable login check).
- Conversation list: `/api/organizations/{org}/chat_conversations_v2?limit=30&offset=N` — each offset is requested twice (`consistency=eventual` then `strong`), so dedupe by uuid; the sidebar only pages further when scroll containers get `scrollTop` set via JS (`mouse.wheel` does nothing).
- Conversation payload: same-origin in-page `fetch` of `/api/organizations/{org}/chat_conversations/{uuid}?tree=True&rendering_mode=messages&...` (org from the `lastActiveOrg` cookie). Visiting `/chat/{uuid}` issues no JSON request — the app preloads via an inline route script.
- Media: `attachments` are inlined text (nothing to download); `files[]` blob bytes are not exposed by any API (24 endpoint probes 404, no UI request, no bundle template), so only their metadata is archived. Downloads cover media-ish URLs found in payloads; third-party fetch failures are recorded in `media_failures`.

## Current state

All 11 plan tasks implemented and verified (`docs/superpowers/plans/2026-10-04-claude-conversation-archiver.md`): 33 tests pass, ruff and pyrefly clean. A full archive run produced 324 conversations / 3,207 messages / 91 MB in `archive/`. Ledger: `.superpowers/sdd/2026-10-04-claude-conversation-archiver/progress.md`. Single package `claude_data`.
