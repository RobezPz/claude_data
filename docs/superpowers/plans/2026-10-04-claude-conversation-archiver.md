# Claude Conversation Archiver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive every Claude.ai conversation as per-conversation JSON plus downloaded media, via a Playwright pipeline that logs in once (email-code), captures the API responses the claude.ai UI itself makes, and resumes without re-downloading archived work.

**Architecture:** Four-stage pipeline (login → enumerate → fetch → store) in `src/claude_data/`. A persistent browser profile at `~/.cache/claude_data/profile/` holds the session. Data comes only from captured JSON API responses — no DOM scraping. Pure logic (paths, atomic writes, URL rewriting, resume diffing, manifest) is unit-tested; browser glue stays thin and is verified manually against the real site.

**Tech Stack:** Python >= 3.13, uv, Playwright (sync API, Chromium), pytest; ruff + pyrefly via uvx.

**Spec:** `docs/superpowers/specs/2026-10-04-claude-conversation-archiver-design.md`

> 2026-10-26 note: this plan was executed as written against a flat `src/claude_data/`.
> The modules were later moved to `src/claude_data/archiver/` and the tests renamed
> with a `test_archiver_` prefix (`tests/test_store.py` → `tests/test_archiver_store.py`,
> etc.). Task text below keeps the original paths as a record of what was executed.

## Global Constraints

- Python >= 3.13; uv only (never pip); deps only via `uv add` (runtime: `playwright`; dev: `pytest`)
- Before every commit: `uvx ruff check` then `uvx pyrefly check` must both pass
- Never print, log, or commit `.env` contents; error messages may name a missing key but never show values
- Never commit `archive/` or `~/.cache/claude_data/`; session profile lives outside the repo
- All archive writes are atomic (temp file in the target directory + `os.replace`) — no partial files ever exist
- Exit code non-zero (1) when any conversation failed; 2 for env/config errors before browser launch
- No HTML/Markdown rendering, no scheduling, no multi-account support (spec YAGNI)
- Spec contract for media: every remote file needed to use a conversation offline is downloaded and referenced locally

## Review Focus

1. Media download fails mid-run → URL stays remote in saved JSON, failure recorded in meta sidecar/manifest, run continues — test in Task 8 (`test_process_conversation_partial_media_failure`)
2. Two media URLs share the same basename → both archived, neither overwrites the other — test in Task 3 (`test_derive_media_filename_collision`)
3. Captured payload has unrecognized/missing fields → extraction degrades to defaults instead of crashing — test in Task 7 (`test_extract_metadata_empty_payload`)
4. `.env` missing `CLAUDE_USERNAME` → clear error message, exit 2, before any browser launch — test in Task 1 (`test_require_username_missing`); wiring order pinned in Task 10
5. Conversation capture fails 3 attempts → conversation listed as failed, run continues, exit 1 — test in Task 9 (`test_fetch_all_capture_failure_retries_then_skips`)

---

### Task 1: Dependencies + env loading (`env.py`)

**Files:**
- Create: `src/claude_data/env.py`
- Create: `tests/test_env.py`
- Modify: `pyproject.toml` (deps, pytest config)

**Interfaces:**
- Consumes: nothing
- Produces: `read_env(path: Path) -> dict[str, str]`; `require_username(env: dict[str, str]) -> str`; `class MissingUsernameError(RuntimeError)`

- [ ] **Step 1: Install deps**

Run: `uv add playwright && uv add --dev pytest && uv run playwright install chromium`
Expected: pyproject gains playwright + pytest; Chromium download completes.

- [ ] **Step 2: Add pytest config to `pyproject.toml`**

`[tool.pytest.ini_options]` with `testpaths = ["tests"]`.

- [ ] **Step 3: Write failing tests in `tests/test_env.py`**

```python
def test_read_env_parses_values_quotes_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nCLAUDE_USERNAME='user@example.com'\nEMPTY=\n")
    env = read_env(env_file)
    assert env == {"CLAUDE_USERNAME": "user@example.com", "EMPTY": ""}

def test_require_username_missing_raises_without_leaking_values():
    with pytest.raises(MissingUsernameError, match="CLAUDE_USERNAME"):
        require_username({"OTHER": "secret-value"})

def test_require_username_present_returns_value():
    assert require_username({"CLAUDE_USERNAME": "user@example.com"}) == "user@example.com"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_env.py -v`
Expected: FAIL — module `claude_data.env` does not exist.

- [ ] **Step 5: Implement `env.py`**

`read_env`: parse KEY=VALUE lines; strip surrounding single/double quotes; skip blank lines and `#` comments; missing file → `{}`. `require_username`: return the value or raise `MissingUsernameError("CLAUDE_USERNAME missing from .env")` — never include other values in the message.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_env.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/env.py tests/test_env.py pyproject.toml uv.lock
git commit -m "feat: env loading with clear missing-username error"
```

### Task 2: Archive layout + atomic writes (`store.py`)

**Files:**
- Create: `src/claude_data/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing
- Produces: `conversation_path(root: Path, uuid: str) -> Path` (`root/conversations/{uuid}.json`); `meta_path(root: Path, uuid: str) -> Path` (`root/conversations/{uuid}.meta.json`); `media_path(root: Path, filename: str) -> Path` (`root/media/{filename}`); `atomic_write_bytes(path: Path, data: bytes) -> None`; `atomic_write_json(path: Path, obj: object) -> None` (indent 2, `ensure_ascii=False`); `list_archived(root: Path) -> set[str]` (uuids of `conversations/*.json`, excluding `*.meta.json`)

- [ ] **Step 1: Write failing tests in `tests/test_store.py`**

Test names and assertions: `test_conversation_and_meta_paths` (exact path strings); `test_atomic_write_json_roundtrips_and_replaces` (write, read back equal, overwrite existing, no `*.tmp*` files left in directory); `test_list_archived_ignores_meta_and_other_files` (two payloads + one `.meta.json` + one stray `.txt` → exactly the two payload uuids).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `store.py`**

Path helpers as pure string joins. Atomic writes: serialize fully in memory, write to `path.with_suffix(path.suffix + ".tmp")` in the same directory, then `os.replace`. `list_archived` filters `*.meta.json` out.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/store.py tests/test_store.py
git commit -m "feat: archive layout and atomic writes"
```

### Task 3: Media URL pure logic (`media.py`)

**Files:**
- Create: `src/claude_data/media.py`
- Create: `tests/test_media.py`

**Interfaces:**
- Consumes: `media_path` from `store.py`
- Produces: `rewrite_urls(obj: object, mapping: dict[str, str]) -> object`; `derive_media_filename(url: str, taken: set[str] = frozenset()) -> str`; `class DownloadPlan` (frozen dataclass: `to_download: list[tuple[str, str]]`, `present: dict[str, str]`); `plan_downloads(urls: list[str], root: Path) -> DownloadPlan`

- [ ] **Step 1: Write failing tests in `tests/test_media.py`**

```python
def test_rewrite_urls_replaces_exact_and_embedded():
    payload = {"a": "https://cdn.example.com/f/1.png",
               "text": "![img](https://cdn.example.com/f/1.png)",
               "nested": [{"b": "https://cdn.example.com/f/2.png"}],
               "untouched": "plain text"}
    out = rewrite_urls(payload, {"https://cdn.example.com/f/1.png": "media/1.png"})
    assert out["a"] == "media/1.png"
    assert out["text"] == "![img](media/1.png)"
    assert out["nested"][0]["b"] == "https://cdn.example.com/f/2.png"
    assert out["untouched"] == "plain text"
    assert payload["a"] == "https://cdn.example.com/f/1.png"  # original not mutated

def test_derive_media_filename_from_url_path():
    assert derive_media_filename("https://cdn.example.com/a/photo.png") == "photo.png"

def test_derive_media_filename_collision():
    url = "https://cdn.example.com/b/photo.png"
    name = derive_media_filename(url, taken={"photo.png"})
    stem, _, suffix = name.rpartition(".")
    assert name != "photo.png" and stem.startswith("photo-") and suffix == "png"
    assert derive_media_filename(url, taken={"photo.png", name}) != name  # never re-collides

def test_derive_media_filename_no_path_uses_hash():
    name = derive_media_filename("https://cdn.example.com/?q=1")
    assert re.fullmatch(r"file-[0-9a-f]{12}", name)

def test_plan_downloads_skips_existing_and_dedupes(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "have.png").write_bytes(b"x")
    plan = plan_downloads(
        ["https://cdn.example.com/have.png", "https://cdn.example.com/have.png",
         "https://cdn.example.com/new.png"], tmp_path)
    assert plan.present == {"https://cdn.example.com/have.png": "media/have.png"}
    assert plan.to_download == [("https://cdn.example.com/new.png", "new.png")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_media.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the three functions in `media.py`**

`rewrite_urls`: recursively rebuild dicts/lists, replacing mapping keys as exact values and as substrings inside longer strings (`str.replace` per key). `derive_media_filename`: basename from `urllib.parse.urlsplit(url).path` via `PurePosixPath`; no usable basename → `file-{sha256(url).hexdigest()[:12]}`; basename in `taken` → `{stem}-{sha256(url).hexdigest()[:8]}{suffix}`. `plan_downloads`: dedupe preserving order; filename exists on disk → `present` entry `media/{filename}`; else `to_download` pair; grow the `taken` set across the batch so basenames never collide within a conversation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_media.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/media.py tests/test_media.py
git commit -m "feat: media filename derivation, download planning, URL rewriting"
```

### Task 4: Browser session + email-code login (`browser.py`, `login.py`)

**Files:**
- Create: `src/claude_data/browser.py`
- Create: `src/claude_data/login.py`

**Interfaces:**
- Consumes: `require_username` from `env.py` (Task 1)
- Produces: `PROFILE_DIR: Path` (`~/.cache/claude_data/profile`); `launch(headless: bool = True) -> BrowserContext` (persistent Chromium context); `is_logged_in(page: Page) -> bool`; `class ResponseCapture` (`__init__(self, pattern: str)` regex over response URLs; `attach(page: Page) -> None`; `objects: list[dict]` accumulates parsed JSON bodies of matching responses); `login(context: BrowserContext, email: str, timeout_s: float = 600.0) -> Page` (fills email, waits up to `timeout_s` for the logged-in state, raises `LoginTimeout` on expiry)

- [ ] **Step 1: Implement `browser.py`**

`launch`: `sync_playwright().chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)` — create the parent dir first. `is_logged_in`: after `page.goto("https://claude.ai/chats")`, logged out iff the URL redirects into the login flow (URL contains `/login`). Selectors and the exact login-URL shape are discovered during Step 3's manual verification.

- [ ] **Step 2: Implement `login.py`**

`login`: new page on the context → `https://claude.ai/login` → fill the email input with `email` → click the continue/submit control → poll `is_logged_in` until success or `timeout_s` expires → return the page. If the email field is not found within 30s, raise `LoginTimeout`. Selectors discovered during manual verification (Step 3) — keep them as module-level constants.

- [ ] **Step 3: Manual verification (human-in-the-loop)**

Run: `uv run python -c "from pathlib import Path; from claude_data.browser import launch; from claude_data.login import login; from claude_data.env import read_env, require_username; ctx = launch(headless=False); login(ctx, require_username(read_env(Path('.env')))); print('LOGGED IN')"`
Expected: headed Chromium opens, the email from `.env` is filled and submitted, you click the button in the received email, the chats page loads, terminal prints `LOGGED IN`. Session persists in `~/.cache/claude_data/profile`.
Second run (headless reuse): `uv run python -c "from claude_data.browser import launch, is_logged_in; ctx = launch(); p = ctx.new_page(); print('LOGGED IN' if is_logged_in(p) else 'LOGGED OUT'); ctx.close()"`
Expected: prints `LOGGED IN` without any interaction.

- [ ] **Step 4: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/browser.py src/claude_data/login.py
git commit -m "feat: persistent browser session and email-code login"
```

### Task 5: Conversation enumeration (`collect.py`)

**Files:**
- Create: `src/claude_data/collect.py`
- Create: `tests/test_collect.py`
- Create: `tests/fixtures/list_response.json` (committed fixture, sanitized)

**Interfaces:**
- Consumes: `ResponseCapture` from `browser.py` (Task 4)
- Produces: `@dataclass(frozen=True) class ConversationRef` (`uuid: str`, `title: str`, `created_at: str`, `updated_at: str`); `parse_list_response(obj: dict) -> list[ConversationRef]`; `collect_conversations(page: Page) -> list[ConversationRef]`

- [ ] **Step 1: Implement the scroll-and-capture half of `collect.py`**

`collect_conversations`: attach a `ResponseCapture` whose regex matches the chat-list API responses the UI makes while scrolling `https://claude.ai/chats`; scroll the "Recent" list (`page.keyboard.press("End")` or wheel-scroll the sidebar — discovered during Step 2) until 3 consecutive scroll attempts yield no new conversation uuids; merge all captured list responses; return `parse_list_response` applied to each merged object, deduped by uuid. Add a module-level `dump_raw(page, path)` helper (used once in Step 2 to save the fixture; it stays as a debug aid, not a CLI flag).

- [ ] **Step 2: Discovery — save the list fixture (human-in-the-loop)**

Run a one-off snippet: launch (reusing the Task 4 session), `collect_conversations` with `dump_raw(page, "tests/fixtures/list_response.json")`.
Expected: the fixture file contains one or more captured chat-list API response objects. Sanitize: replace titles/uuids/timestamps with synthetic values (keep structure and field names). Note the media/list field names you observed for Task 7.

- [ ] **Step 3: Write failing tests in `tests/test_collect.py`**

`test_parse_list_response_returns_refs` — assert the exact uuid/title/timestamp values from the sanitized fixture (literal expected values); `test_parse_list_response_empty_and_alien` — `{}` and `{"unrelated": []}` → `[]` (no crash).

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_collect.py -v`
Expected: FAIL — parse not implemented.

- [ ] **Step 5: Implement `parse_list_response`**

Extract uuid/title/created_at/updated_at from the response object using the fields observed in the fixture; per-ref: any missing field → `""`. Empty/unknown shapes → `[]`, never an exception.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_collect.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/collect.py tests/test_collect.py tests/fixtures/list_response.json
git commit -m "feat: Recent-list enumeration with fixture-pinned parser"
```

### Task 6: Conversation payload capture (`fetch.py` capture half)

**Files:**
- Create: `src/claude_data/fetch.py`
- Create: `tests/fixtures/conversation.json` (committed fixture, sanitized)

**Interfaces:**
- Consumes: `ResponseCapture` from `browser.py` (Task 4)
- Produces: `capture_payload(page: Page, uuid: str, timeout_s: float = 60.0) -> dict | None` (visits `https://claude.ai/chat/{uuid}`, captures the conversation API response(s) for that chat, returns the merged/primary payload object; `None` on timeout or no match)

- [ ] **Step 1: Implement `capture_payload` in `fetch.py`**

Attach a `ResponseCapture` regex matching the per-conversation API URL for `{uuid}`; navigate; wait up to `timeout_s` for at least one matching object; return the first (if the UI splits loading across multiple responses, merge them during Step 2 discovery and pin that merge in code). Return `None` on timeout.

- [ ] **Step 2: Discovery — save the conversation fixture (human-in-the-loop)**

Run a one-off snippet: `capture_payload(page, "2098eb49-0fcc-4fb9-8050-57e652b6482d")` (the README's first conversation) → write raw result to `tests/fixtures/conversation.json`.
Expected: the fixture contains the full conversation payload. Sanitize to synthetic values; keep structure and field names. Record which fields hold message content, attachments/media URLs, timestamps, and title — Task 7 uses this.

- [ ] **Step 3: Manual verification**

Re-run the snippet for the README's last conversation uuid (`7fbe298a-41fd-46b5-ba34-5a099c8f07ba`) → a payload returns (not `None`), with the same response shape. Do not commit that capture.

- [ ] **Step 4: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/fetch.py tests/fixtures/conversation.json
git commit -m "feat: conversation payload capture with sanitized fixture"
```

### Task 7: Payload extraction + manifest (`store.py` metadata half, `media.py` extraction)

**Files:**
- Modify: `src/claude_data/store.py`
- Modify: `src/claude_data/media.py`
- Create: `tests/test_extraction.py`
- Create: `tests/test_manifest.py`

**Interfaces:**
- Consumes: fixtures from Tasks 5–6; `meta_path`, `conversation_path` from `store.py`
- Produces: `extract_metadata(payload: dict) -> dict` (`{"title": str, "created_at": str, "updated_at": str, "message_count": int}`; missing fields → `""` / `0`); `extract_media_urls(payload: dict) -> list[str]` (deduped, order-preserving); `build_manifest(root: Path) -> dict` (`{"generated_at": <utc iso>, "conversations": [{uuid, title, created_at, updated_at, message_count, source_url, media, media_failures}]}` sorted by uuid)

- [ ] **Step 1: Write failing tests in `tests/test_extraction.py`**

`test_extract_metadata_from_fixture` — assert the exact title/timestamp/message-count values from the sanitized `tests/fixtures/conversation.json` (literal expected values). `test_extract_metadata_empty_payload` — `{}` → `{"title": "", "created_at": "", "updated_at": "", "message_count": 0}` (no crash). `test_extract_media_urls_from_fixture` — assert the exact deduped URL list the fixture contains (literal values, as identified during sanitization in Task 6). `test_extract_media_urls_alien_payload` — `{"x": 1}` → `[]`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_extraction.py -v`
Expected: FAIL — functions do not exist.

- [ ] **Step 3: Implement `extract_metadata` in `store.py` and `extract_media_urls` in `media.py`**

Use exactly the fields observed in the fixture (Task 6 discovery). `extract_media_urls`: walk the payload tree; collect http(s) URLs from the observed media/attachment fields (plus the URL pattern noted during discovery); never raise on unexpected shapes — skip what isn't recognized.

- [ ] **Step 4: Write failing test in `tests/test_manifest.py`**

`test_build_manifest_from_disk` — build a temp `root` with one payload file (`conversations/<uuid>.json`, the fixture) + its `.meta.json` sidecar (`{"media": {"https://cdn.example.com/f/1.png": "media/1.png"}, "media_failures": ["https://cdn.example.com/f/2.png"]}`); assert the manifest entry equals `{"uuid": <uuid>, "title": <fixture title>, "created_at": …, "updated_at": …, "message_count": …, "source_url": "https://claude.ai/chat/<uuid>", "media": {…sidecar…}, "media_failures": […sidecar…]}` and `generated_at` parses as UTC ISO-8601; a second payload without a sidecar → empty `media`/`media_failures` defaults; entries sorted by uuid.

- [ ] **Step 5: Run test to verify it fails, then implement `build_manifest` in `store.py`**

Iterate `conversations/*.json` (exclude `*.meta.json`), apply `extract_metadata`, read the sidecar if present (missing → empty defaults), assemble entries, `generated_at = datetime.now(timezone.utc).isoformat()`.

- [ ] **Step 6: Run all tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS (all tests so far).

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/store.py src/claude_data/media.py tests/test_extraction.py tests/test_manifest.py
git commit -m "feat: payload metadata/media extraction and manifest build"
```

### Task 8: Media download + per-conversation pipeline (`media.py` download half, `fetch.py` process half)

**Files:**
- Modify: `src/claude_data/media.py`
- Modify: `src/claude_data/fetch.py`
- Create: `tests/test_process.py`

**Interfaces:**
- Consumes: `plan_downloads`, `rewrite_urls`, `extract_media_urls` (Tasks 3, 7); `atomic_write_json`, `conversation_path`, `meta_path` (Task 2); `capture_payload` (Task 6)
- Produces: `download_media(context: BrowserContext, downloads: list[tuple[str, str]]) -> tuple[dict[str, str], list[str]]` (successes map url→`media/{filename}` via `context.request.get(url)` + `atomic_write_bytes`; failures collected, never raised); `process_conversation(root: Path, uuid: str, capture: Callable[[str], dict | None], download: Callable[[list[tuple[str, str]]], tuple[dict[str, str], list[str]]]) -> None`; `class CaptureError(RuntimeError)`

- [ ] **Step 1: Write failing tests in `tests/test_process.py`**

```python
def test_process_conversation_writes_rewritten_payload_and_meta(tmp_path):
    payload = {"title": "t", "msgs": [{"text": "see https://cdn.example.com/a/1.png"}]}
    capture = lambda uuid: payload
    download = lambda pairs: ({"https://cdn.example.com/a/1.png": "media/1.png"}, [])
    process_conversation(tmp_path, "uuid-1", capture, download)
    saved = json.loads((tmp_path / "conversations" / "uuid-1.json").read_text())
    assert saved["msgs"][0]["text"] == "see media/1.png"
    meta = json.loads((tmp_path / "conversations" / "uuid-1.meta.json").read_text())
    assert meta == {"media": {"https://cdn.example.com/a/1.png": "media/1.png"},
                    "media_failures": []}

def test_process_conversation_partial_media_failure(tmp_path):
    payload = {"m": ["https://cdn.example.com/a/1.png", "https://cdn.example.com/a/2.png"]}
    download = lambda pairs: ({"https://cdn.example.com/a/1.png": "media/1.png"},
                              ["https://cdn.example.com/a/2.png"])
    process_conversation(tmp_path, "u", lambda uuid: payload, download)
    saved = json.loads((tmp_path / "conversations" / "u.json").read_text())
    assert saved["m"] == ["media/1.png", "https://cdn.example.com/a/2.png"]
    meta = json.loads((tmp_path / "conversations" / "u.meta.json").read_text())
    assert meta["media_failures"] == ["https://cdn.example.com/a/2.png"]

def test_process_conversation_capture_none_raises(tmp_path):
    with pytest.raises(CaptureError):
        process_conversation(tmp_path, "u", lambda uuid: None, lambda pairs: ({}, []))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process.py -v`
Expected: FAIL — functions do not exist.

- [ ] **Step 3: Implement `process_conversation` in `fetch.py` and `download_media` in `media.py`**

`process_conversation`: capture → `CaptureError` on `None`; `extract_media_urls` → `plan_downloads` → `download(plan.to_download)` → mapping = `plan.present | successes` → `rewrite_urls(payload, mapping)` → `atomic_write_json` payload + `{"media": mapping, "media_failures": sorted(failures)}` sidecar. `download_media`: fetch each url via the authenticated browser context; non-OK/exception → failure list (skip existing files defensively).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Manual verification of the real download path (human-in-the-loop)**

One-off snippet: reuse the session, `capture_payload` the README's first conversation, run `process_conversation` with the real `download_media` against a temp root; confirm media files land on disk and payload/meta files are valid JSON. Delete the temp root afterwards.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/media.py src/claude_data/fetch.py tests/test_process.py
git commit -m "feat: media download and per-conversation pipeline"
```

### Task 9: Retry/resume loop (`fetch.py` orchestration half)

**Files:**
- Modify: `src/claude_data/fetch.py`
- Create: `tests/test_fetch_all.py`

**Interfaces:**
- Consumes: `list_archived` from `store.py` (Task 2); `ConversationRef` from `collect.py` (Task 5); `CaptureError` (Task 8)
- Produces: `@dataclass(frozen=True) class FetchReport` (`fetched: list[str]`, `failed: list[str]`, `skipped: int`); `fetch_all(refs: list[ConversationRef], root: Path, fetch_one: Callable[[str], None], force: bool = False, refresh_uuids: frozenset[str] = frozenset()) -> FetchReport`

- [ ] **Step 1: Write failing tests in `tests/test_fetch_all.py`**

```python
def make_ref(uuid):
    return ConversationRef(uuid=uuid, title="", created_at="", updated_at="")

def test_fetch_all_skips_archived_and_resumes_missing(tmp_path):
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "done.json").write_text("{}")
    calls = []
    report = fetch_all([make_ref("done"), make_ref("todo")], tmp_path,
                       fetch_one=lambda uuid: calls.append(uuid))
    assert calls == ["todo"]
    assert report == FetchReport(fetched=["todo"], failed=[], skipped=1)

def test_fetch_all_refresh_and_force_refetch_existing(tmp_path):
    (tmp_path / "conversations").mkdir(); (tmp_path / "conversations" / "done.json").write_text("{}")
    calls = []
    report = fetch_all([make_ref("done")], tmp_path, fetch_one=lambda u: calls.append(u) or None,
                       refresh_uuids=frozenset({"done"}))
    assert calls == ["done"]
    report = fetch_all([make_ref("done")], tmp_path,
                       fetch_one=lambda u: calls.append(u) or None, force=True)
    assert calls == ["done", "done"] and report.skipped == 0

def test_fetch_all_capture_failure_retries_then_skips(tmp_path):
    attempts = []
    def failing(uuid):
        attempts.append(uuid)
        raise CaptureError(uuid)
    report = fetch_all([make_ref("bad")], tmp_path, fetch_one=failing)
    assert len(attempts) == 3
    assert report.failed == ["bad"] and report.fetched == []

def test_fetch_all_success_after_one_retry(tmp_path):
    calls = []
    def flaky(uuid):
        calls.append(uuid)
        if len(calls) == 1:
            raise CaptureError(uuid)
    report = fetch_all([make_ref("u")], tmp_path, fetch_one=flaky)
    assert report.fetched == ["u"] and report.failed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetch_all.py -v`
Expected: FAIL — functions do not exist.

- [ ] **Step 3: Implement `fetch_all`**

Skip rule: uuid in `list_archived(root)` and not `force` and uuid not in `refresh_uuids` → count skipped. Otherwise up to 3 attempts calling `fetch_one(uuid)`; any exception (incl. `CaptureError`) → retry; exhausted → `failed`. Success → `fetched`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetch_all.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/fetch.py tests/test_fetch_all.py
git commit -m "feat: fetch loop with retry, resume, and refresh"
```

### Task 10: CLI + pipeline wiring (`__init__.py`)

**Files:**
- Modify: `src/claude_data/__init__.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above (exact names as produced in Tasks 1–9)
- Produces: `parse_args(argv: list[str] | None = None) -> argparse.Namespace` (`login: bool`, `headed: bool`, `refresh: str | None` — `None` default, `"all"` when flag given bare, else the uuid string); `main() -> None` (console-script entry)

- [ ] **Step 1: Write failing tests in `tests/test_cli.py`**

```python
def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.login is False and ns.headed is False and ns.refresh is None

def test_parse_args_login_headed():
    ns = parse_args(["--login", "--headed"])
    assert ns.login and ns.headed and ns.refresh is None

def test_parse_args_refresh_all_and_single():
    assert parse_args(["--refresh"]).refresh == "all"
    assert parse_args(["--refresh", "2098eb49-0fcc-4fb9-8050-57e652b6482d"]).refresh == "2098eb49-0fcc-4fb9-8050-57e652b6482d"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `parse_args` does not exist.

- [ ] **Step 3: Implement `parse_args` in `__init__.py`**

argparse with `--login`/`--headed` (`action="store_true"`) and `--refresh` (`nargs="?", const="all", default=None, metavar="UUID"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Implement `main()` wiring**

Order (pinned): load `.env` + `require_username` (missing → print message to stderr, `sys.exit(2)` — before any browser launch); decide headless: headed iff `args.login or args.headed`; launch context; probe `is_logged_in` — if logged out and headless, close and relaunch headed; if `args.login` or logged out → `login(context, username)`; mid-run logged-out abort: if a capture suddenly yields `None`s across the board, stop with a message telling the user to re-run with `--login`; `collect_conversations(page)`; `refresh_uuids = frozenset({args.refresh}) if args.refresh not in (None, "all") else frozenset()`; `force = args.refresh == "all"`; `fetch_all(refs, root=Path("archive"), fetch_one=<closure: `process_conversation` with `capture_payload(page, …)` and real `download_media(context, …)>`, force=force, refresh_uuids=refresh_uuids)`; `atomic_write_json(Path("archive/index.json"), build_manifest(Path("archive")))`; print summary (fetched/skipped/failed counts + failed uuids); `sys.exit(1 if report.failed else 0)`.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uvx ruff check && uvx pyrefly check
git add src/claude_data/__init__.py tests/test_cli.py
git commit -m "feat: CLI wiring for full pipeline"
```

### Task 11: Full acceptance run

**Files:** none new (verification only)

**Interfaces:**
- Consumes: the complete CLI (Task 10)
- Produces: a verified archive on disk (not committed)

- [ ] **Step 1: Run the full pipeline (human-in-the-loop)**

Run: `uv run claude-data`
Expected: if the session is stale, headed login happens first; enumeration scrolls to the end; conversations fetch (skipping none on first run); summary prints fetched=N skipped=0 failed=0; exit 0.

- [ ] **Step 2: Verify README acceptance criteria**

Check `archive/index.json`: the uuid `2098eb49-0fcc-4fb9-8050-57e652b6482d` (title "Agentic coding models guide") and `7fbe298a-41fd-46b5-ba34-5a099c8f07ba` ("Creating SQL database from JSON") are both present, plus every conversation between; counts match the summary; `archive/conversations/` and `archive/media/` populated; spot-check one conversation JSON: valid JSON, media references are local `media/...` paths and those files exist.

- [ ] **Step 3: Verify resume (re-run)**

Run: `uv run claude-data` again.
Expected: summary prints fetched=0 skipped=N failed=0 (nothing re-downloaded), exit 0; `archive/` unchanged except `index.json` regeneration timestamp.

- [ ] **Step 4: Offline spot-check**

Disable networking; open a conversation JSON and its media file from disk.
Expected: fully readable with no network.

- [ ] **Step 5: Final green check**

Run: `uv run pytest -v && uvx ruff check && uvx pyrefly check`
Expected: all tests PASS, lint clean, typecheck clean.
