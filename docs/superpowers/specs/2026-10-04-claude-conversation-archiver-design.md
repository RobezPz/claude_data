# Claude Conversation Archiver — Design Spec

Date: 2026-10-04
Status: Approved in brainstorming; pending implementation plan

> 2026-10-26 layout note: the acquisition modules described below now live in
> `src/claude_data/archiver/` (e.g. `archiver/store.py`, `archiver/fetch.py`),
> and their tests carry the `test_archiver_` prefix (`tests/test_archiver_store.py`,
> `tests/test_archiver_media.py`, ...). The behavior is unchanged; only the paths moved.

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
- Subsequent runs: reuse the session, still **headed**. (Amended after
  implementation: Cloudflare challenges every headless build, so headless
  reuse does not work; the window stays visible for the whole run.)

## Components (src/claude_data/)

- `__init__.py` — `main()`: CLI parsing (argparse) + orchestration; remains
  the console-script entry.
- `browser.py` — persistent-context session management: headed launch (the
  only mode Cloudflare allows) with automation flags stripped, logged-out
  detection, response-capture hook.
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
- `--headed` — accepted for compatibility; runs are always visible.

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
- Scheduling, multi-account support, headless operation (Cloudflare-blocked;
  would need a stealth browser build).

## Amendment (2026-10-25): on-demand files

The original design deferred uploaded files. Two on-demand passes now operate
on the existing archive without re-enumerating conversations:

- `--download-files [--max-size-mb N] [--conversation UUID] [--force] [--dry-run]`
  downloads uploaded `files[]` images/PDFs and assistant `present_files` zips.
- `--export-artifacts [--conversation UUID] [--force] [--dry-run]` exports
  inline `artifacts` tool content; it needs no browser or `.env`.

Output additions: `archive/files/{uuid}/`, `archive/generated/{uuid}/files-N.zip`,
and `archive/artifacts/{uuid}/`. `files[]` entries gain `local_path` while
retaining their remote URLs. `index.json` gains `files`, `file_failures`,
`artifacts`, `generated`, and `generated_failures`.

Uploaded images expose only WebP previews (~952 px median; `/original`,
`/content`, and `/download` are 404), so previews are archived. Document entries
expose the original PDF via `document_pdf`. `blob` uploads and `SendUserFile`
deliveries have no working download endpoint and are recorded as unavailable.
`present_files` batches come from
`/api/organizations/{org}/conversations/{uuid}/wiggle/download-files?paths=...`
as zips; batches are capped at ~8 paths, with a per-path fallback because one
missing path aborts the whole batch.

## Amendment (2026-10-26): one `sync` command

The on-demand passes above duplicated the launch/login/minimize block and made
the common case (fetch new conversations, then their files) a two-step
incantation. The CLI is now two peer subcommands mirroring `view`:

- `claude-data sync [--login] [--refresh [UUID]] [--show-browser]
  [--conversation UUID] [--max-size-mb N] [--force] [--dry-run]` — one browser
  session drives every acquisition pass in order: launch and minimize, log in
  if required, enumerate and fetch conversations plus their media, then
  uploaded files and `present_files` zips, then export inline artifacts with
  the browser closed, then rebuild `index.json`. Progress is reported as one
  summary (conversations, files, generated, artifacts).
- `claude-data view [--port PORT] [--archive PATH] [--data-dir PATH] [--open]` —
  unchanged except that the browser is now opt-in via `--open`; the default is
  server-only.

`--download-files`, `--export-artifacts`, `--headed`, and `view --no-browser`
are removed. A bare `claude-data` is a usage error naming both commands, and
each command rejects the other's flags. `--conversation` still scopes only the
file/generated/artifact passes; `--refresh` now composes with them because
every `sync` runs them. `--dry-run` never launches a browser or writes: it
plans the file/generated/artifact passes from the existing archive, so it is
mutually exclusive with `--login` and `--refresh`. Exit codes: 0 clean, 1 if
any conversation/file/generated pass failed (or `lastActiveOrg` is unreadable),
2 for configuration errors (missing `CLAUDE_USERNAME`, unknown
`--conversation`).

All writes stay atomic and skip-if-present; `--force` overwrites. The
`GET`-only rules from the 2026-10-25 amendment still apply.

## Amendment (2026-10-26): delivered text files

`SendUserFile` deliveries showed up as `files[]` entries with
`file_kind: "blob"` and only a sandbox `path`, so every one was recorded as
unavailable. Plain `GET`s to `/files/{uuid}` (with and without
`/download`, `/content`, `/original`, `/raw`, `/file`, `/preview`,
`/document_pdf`), `/blobs/...`, `/uploads/...`, conversation-scoped paths, and
the `wiggle/download-files` endpoint all return 404 for these UUIDs, so there is
no remote bytes source.

The transcript, however, contains the full text. When a blob's basename
matches a later `Write` (`file_path`, `content`) or `create_file` (`path`,
`file_text`) tool call, the file pass now writes that content to
`files/{uuid}/{file_uuid}-{name}` and reports status `exported`; dry-run
reports `planned`. The last write wins. If an `Edit`/`MultiEdit`/`str_replace`
touches the path after its last write, the blob stays unavailable (with reason
"delivered file was edited after creation") rather than archiving stale bytes.
Binary deliveries and blobs produced by shell commands remain unavailable.
Recovered entries get `local_path` in the payload, clear the meta
`file_failures` entry, and surface as file assets in the curator.

## Amendment (2026-10-26): file provenance labels

`files/` mixes two kinds of attachments: things the person uploaded and files
Claude delivered (`SendUserFile`, stored as a `files[]` blob). The folder split
stays as it is — the payload itself models both as `files[]` entries on a
message — but each record now carries an `origin`.

The carrier message's `sender` is the discriminator: `human` means the file was
uploaded (`origin: "user"`), `assistant` means Claude delivered it
(`origin: "assistant"`). A `files[]` entry with no recognisable sender gets no
`origin`. `extract_file_refs()` sets it from the message, `update_meta()` writes
it into `conversations/{uuid}.meta.json` (omitted when unknown), and
`build_manifest()` copies it into `index.json` unchanged.

The curator does not depend on the backfill: `ClaudeAdapter` derives `origin`
from the same `sender` field while loading the payload, so labels appear
immediately. `generated` and `artifact` assets are always `assistant` (they
only exist because Claude produced them); `media` assets stay unlabelled
because an external citation can come from either side. The curator UI renders
`origin` as a small muted badge (`user` / `assistant`) after each attachment.
Exported bundles are unchanged.
