# Claude Conversation Archiver — Design Spec

Date: 2026-10-04
Status: Approved in brainstorming; pending implementation plan

## Purpose

Archive every conversation from a Claude.ai account for complete offline use
(README.md is the original prompt/spec). The archive is a structured JSON
export — one file per conversation — plus downloaded media, so the archive is
self-contained and usable programmatically without any network access.

## Validated decisions

- **Login**: Email-code (magic-link) login. `CLAUDE_USERNAME` from `.env`
  is entered automatically; the human completes the login by clicking the
  emailed button while the browser waits, headed.
- **Output**: JSON export — raw captured API payload per conversation plus
  a manifest. No HTML/markdown rendering.
- **Fidelity**: Full — messages, artifacts (as text), and all media
  (images/attachments) downloaded locally.
- **Extraction**: Approach B — Playwright drives the UI (login, scrolling
  "Recent"), but data comes from capturing the internal API responses the
  claude.ai UI itself makes when loading the chat list and each
  conversation. No DOM scraping.

## Architecture

Four-stage pipeline: **login → enumerate → fetch → store**, orchestrated by
the `claude-data` CLI (console script → `main()` in
`src/claude_data/__init__.py`).

A persistent browser profile at `~/.cache/claude_data/profile/` (outside the
repo, never committed) holds the authenticated session:

- First run (or expired session, or `--login`): launch **headed**, go to
  claude.ai, enter the email from `.env`, submit, then wait (generous
  timeout: 10 minutes) while the human completes the emailed-code login.
  Success is detected by the chats page loading.
- Subsequent runs: reuse the session **headless**.

## Components (src/claude_data/)

- `__init__.py` — `main()`: CLI parsing (argparse) + orchestration; remains
  the console-script entry.
- `browser.py` — persistent-context session management: launch (headed for
  login, headless otherwise), logged-out detection, response-capture hook.
- `login.py` — the email-code interactive login flow.
- `collect.py` — open `https://claude.ai/chats`, auto-scroll "Recent"
  until 3 consecutive scroll attempts yield no new conversations,
  capturing the list responses → full conversation inventory (uuid, title,
  timestamps).
- `fetch.py` — for each conversation not already archived: visit
  `https://claude.ai/chat/{uuid}`, capture the conversation payload
  response(s), up to 3 attempts on failure, save atomically.
- `media.py` — extract download-worthy URLs from a captured payload,
  download each via the authenticated page context into `archive/media/`
  (skip if the file already exists), rewrite those URLs in the saved JSON
  to relative local paths, record the remote→local mapping.
- `store.py` — archive layout, atomic writes (build in memory → temp file →
  rename), manifest generation.

The concrete JSON field mapping inside captured payloads (message tree,
attachment references, timestamps) is data-driven: discovered from real
captured payloads during implementation and pinned down by unit tests
against saved fixture payloads. The contract this module must satisfy:
every remote file needed to use the conversation offline is downloaded and
referenced locally.

## Output layout

```
archive/
  conversations/{chat-uuid}.json   # captured payload; URLs of downloaded media rewritten local
  media/{filename}                 # downloaded attachments/images; filename
                                   # derived from the URL path (content-hash
                                   # suffix if the URL has none)
  index.json                       # manifest, rebuilt from disk every run
```

Manifest entry per conversation: uuid, title, created/updated timestamps,
message count, source URL, media mapping (remote URL → local path), media
download failures.

## Resume / re-run semantics

- **File exists = fully captured.** Atomic writes guarantee no partial
  files; an interrupted run leaves at most the in-flight conversation
  absent, and the next run fetches it.
- **Re-runs skip archived work.** Every run enumerates the full list, then
  fetches only conversations missing from `archive/conversations/`.
  Already-archived conversations are never re-downloaded. Media likewise
  skips existing files.
- **`--refresh [UUID]`** forces re-fetch — no argument re-fetches all
  conversations (for conversations that continued online); with a UUID,
  re-fetches just that one. Overwrites via the same atomic write.
- `index.json` is regenerated each run from the files on disk, so it always
  reflects the entire archive.

## CLI surface

- `uv run claude-data` — full pipeline (auto-triggers headed login if the
  session is missing/expired).
- `--login` — force a fresh interactive login.
- `--refresh [UUID]` — re-fetch all, or one conversation.
- `--headed` — run visibly (debugging).

No other flags.

## Error handling

- Capture failure: up to 3 attempts per conversation, then log, skip,
  continue. Final summary lists failures; exit code is non-zero if anything
  failed.
- Media download failure: the URL stays remote in the JSON, the failure is
  recorded in the manifest, the run continues.
- Logged-out mid-run: abort with a clear message telling the user to re-run
  with `--login`.
- `.env` contents are never printed or logged.

## Testing

- **Unit tests (pytest)** on the pure logic: media-URL extraction,
  URL rewriting, manifest build, pending-conversation diffing (resume
  semantics). Fixtures are sanitized copies of real captured payloads,
  saved during implementation.
- **Browser flows** stay thin and are verified manually; they are not
  unit-tested.
- **Acceptance criteria (from README)**: the archive contains every
  conversation from "Agentic coding models guide" (first,
  `2098eb49-0fcc-4fb9-8050-57e652b6482d`) through "Creating SQL database
  from JSON" (last, `7fbe298a-41fd-46b5-ba34-5a099c8f07ba`); spot-check
  confirms JSON + media are usable with no network.

## Dependencies & toolchain

- Runtime dep: `playwright` (Chromium installed via `playwright install`).
- Dev dep: `pytest`.
- uv only (never pip); lint `uvx ruff check`; typecheck `uvx pyrefly check`
  (lint before typecheck). Python >= 3.13, src layout, `uv_build` backend.

## Out of scope (YAGNI)

- HTML/Markdown rendering or any viewer.
- Incremental conversation diffing/merging (re-fetch replaces wholesale via
  `--refresh`).
- Scheduling, multi-account support, headless first-run login.
