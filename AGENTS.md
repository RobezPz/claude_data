# AGENTS.md

## What this repo is

`claude-data` archives every Claude.ai conversation into a self-contained JSON export: one raw payload per conversation plus downloaded media, uploaded files, generated-file zips, and exported artifacts. There is deliberately **no HTML/Markdown viewer** (out of scope; consumers read the JSON and manifest). `README.md` is the original prompt/spec, `docs/superpowers/specs/2026-10-04-claude-conversation-archiver-design.md` is the approved design, and the gitignored `.superpowers/sdd/2026-10-04-claude-conversation-archiver/progress.md` records implementation rulings — read the ledger before changing browser/API behavior.

## Commands

- `uv sync` — create/sync `.venv` (uv only, never pip; add deps with `uv add`)
- `uv run playwright install chromium` — one-time browser install for a fresh environment
- `uv run pytest` — full suite (~0.2 s, no browser/network); single test: `uv run pytest tests/test_media.py::test_plan_downloads_skips_existing_and_dedupes`
- `uvx ruff check` then `uvx pyrefly check` — required before commits; neither is a project dependency and neither has a config file. Pyrefly logs a "No pyrefly.toml" info line but exits 0 when clean (0 errors).
- `uv run claude-data` — real archive run. Opens a **visible** browser and can block on an emailed-code login; never use it as part of automated verification. The window is minimized via CDP immediately after launch; `--show-browser` keeps it visible, and login/Cloudflare restores it automatically.
- `uv run claude-data --login` / `--refresh [UUID]` — force re-auth / re-fetch all (bare) or one conversation. `--headed` is accepted but changes nothing.
- `uv run claude-data --download-files [--max-size-mb N] [--conversation UUID] [--force] [--dry-run]` — download uploaded files and generated `present_files` zips from the **existing** archive. It scans `archive/conversations/*.json`; it does not re-enumerate conversations and only needs the headed browser for network access.
- `uv run claude-data --export-artifacts [--conversation UUID] [--force] [--dry-run]` — export inline `artifacts` content; no browser or `.env` required. Both file commands can be combined.

## Toolchain

Python >= 3.13, src layout, `uv_build` backend, single package `claude_data`; only dev dependency is pytest. README mandates uv + ruff + pyrefly, lint before typecheck.

## Wiring

- Entry point is `main()` in `src/claude_data/__init__.py` (console script `claude-data`): parse args → load `.env` → launch → login → enumerate → fetch → rebuild manifest. `run_file_tasks()` handles the on-demand file/artifact modes.
- `browser.py` persistent Chromium context/response capture; `login.py` email-code flow; `collect.py` scrolls "Recent" and parses list responses; `fetch.py` capture/retry/resume loop (also `read_org`); `media.py` external-media URLs; `files.py` uploaded-file discovery/download; `generated.py` `present_files` zip batches; `artifacts.py` inline artifact export; `store.py` archive paths, atomic writes, manifest.
- Archive layout (gitignored, regenerable):
  - `conversations/{uuid}.json`, `conversations/{uuid}.meta.json` (media/files/artifacts/generated mappings + failures)
  - `media/{filename}` — external citation/link media
  - `files/{uuid}/{file_uuid}-{name}` — uploaded images (WebP preview) and PDF originals
  - `generated/{uuid}/files-N.zip` — assistant `present_files` batches
  - `artifacts/{uuid}/{artifact_id}.{ext}` plus `{artifact_id}.meta.json`
  - `index.json` — manifest, rebuilt from disk each run
- Downloaded uploaded files get a `local_path` field in `files[]`; the original remote URLs are preserved. Existing local files are skipped on re-runs; `--force` overwrites.
- Resume rule: a conversation `.json` existing means complete (writes are atomic). Every archive run enumerates all conversations but fetches only missing uuids; `--refresh` overrides. `--download-files` never changes which conversations are archived.

## Hard-won runtime facts

- Chromium must run **headed**: Cloudflare challenges every headless build. Launch needs `ignore_default_args=["--enable-automation"]` plus `--disable-blink-features=AutomationControlled`; a challenge page is detected by its title starting with "Just a moment..." (the URL alone is not reliable).
- List API: `/api/organizations/{org}/chat_conversations_v2?limit=30&offset=N`. Each offset is requested twice (`consistency=eventual` then `strong`), so dedupe by uuid. The sidebar only pages when overflow containers get `scrollTop` via JS; `mouse.wheel` does nothing.
- Conversation payload: same-origin in-page `fetch` of `/api/organizations/{org}/chat_conversations/{uuid}?tree=True&rendering_mode=messages&...`, taking `org` from the `lastActiveOrg` cookie. Visiting `/chat/{uuid}` issues no JSON request — the app preloads via an inline route script.
- Login is interactive: `CLAUDE_USERNAME` is typed automatically, then a human clicks the emailed link (10-minute timeout). The session lives in `~/.cache/claude_data/profile/`; deleting it forces a new login.
- The archiver minimizes the headed window through CDP (`Browser.setWindowBounds` → `minimized`) right after launch. It is still a headed browser, Cloudflare still passes, and `document.visibilityState` stays `visible`, so paging/fetching is not throttled. The window flashes briefly during launch; `--show-browser` disables minimization, and the window is restored for login/Cloudflare interaction.
- Five consecutive capture failures abort the run with a "re-run with `--login`" message (session assumed logged out).
- `attachments[]` are inline text (`extracted_content`) — nothing to download.
- Uploaded `files[]` images expose `preview_url`/`thumbnail_url`; a plain GET returns WebP at ~952 px median width. Original image bytes are **not** exposed (`/original`, `/content`, `/download` 404). `document` entries expose `document_asset.url` (`document_pdf`, the original PDF). `blob` entries have only a sandbox `path`; no file endpoint works for them.
- File endpoints are `/api/{org}/files/{uuid}/{preview|thumbnail|document_pdf}` — **not** `/api/organizations/{org}/...`. They reject `HEAD` and `Range` with 404; use a plain GET.
- Generated `present_files` files download from `/api/organizations/{org}/conversations/{uuid}/wiggle/download-files?paths=...` as `application/zip`. Keep batches to ~8 paths (long URLs abort). A missing path aborts the whole batch, so fall back to one request per path and merge the successful zips.
- `SendUserFile` file UUIDs and `blob` paths have no working download endpoint; they are recorded as unavailable rather than retried forever.
- External media (absolute citation/link URLs) is still downloaded during the archive run, not by `--download-files`.

## Secrets and generated data

- `.env` contains only `CLAUDE_USERNAME` and is gitignored — never commit, print, or log its contents. A missing key exits 2 before the browser starts.
- `archive/` is generated output — never commit. `~/.cache/claude_data/profile/` is outside the repo.
- **Never persist raw Playwright exception strings**: `APIRequestContext` errors embed the full cookie/session headers. Use `files.error_reason()`, which keeps our own `DownloadError`/`OSError` messages and reduces unknown exceptions to a class name.
- Browser flows are manually verified only; unit tests cover pure logic (parsing, planning, rewriting, resume, manifest, artifact/zip export) against synthetic payloads.

## Testing notes

- `tests/fixtures/conversation.json` intentionally has no media URLs; media extraction is tested with inline payloads. Don't treat the fixture as the full payload shape.
- `tests/test_files.py`, `tests/test_generated.py`, `tests/test_artifacts.py`, `tests/test_fetch.py`, and `tests/test_browser.py` cover the on-demand passes and window minimization; they inject fake fetchers/CDP sessions, so no browser is needed.
- No CI, pre-commit, ruff, or pyrefly config exists; defaults apply.

## Status (2026-10-25)

88 tests pass; ruff and pyrefly are clean. The local archive holds 348 conversations / 3,371 messages / ~157 MB, including 93 uploaded files (92 WebP previews + 1 PDF; 8 blobs unavailable), 19 exported artifacts, and 55 generated-file zips. Implementation plan: `docs/superpowers/plans/2026-10-04-claude-conversation-archiver.md`.
