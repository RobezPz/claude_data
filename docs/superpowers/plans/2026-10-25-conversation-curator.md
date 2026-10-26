# Conversation Curator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local web editor that composes immutable archived Q&A pairs into ordered, date-aware topic collections, with Markdown/HTML/JSON export.

**Architecture:** A provider adapter normalizes archived payloads into sources, messages, pairs, and assets. A stdlib HTTP server exposes a read-only library plus a revisioned collections overlay. A vanilla-JS single-page UI edits collections. Exports produce a self-contained bundle.

**Tech Stack:** Python 3.13, `http.server`, `markdown`, `bleach`, vanilla HTML/CSS/JS, pytest, uv, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-25-conversation-curator-design.md`

## Global Constraints

- Python >= 3.13, uv only, src layout, `uv_build` backend.
- Add runtime deps with `uv add markdown bleach`; no Node/npm, no CDN, no build step.
- Bind the server to `127.0.0.1` only; never fetch external URLs at runtime.
- `archive/` is immutable. `curated/` is user data, gitignored, and must survive re-archives.
- Collections are saved atomically through one revisioned `PUT /api/state`; stale writes get `409`.
- Use the exact names, signatures, and paths in each task's **Interfaces** block.
- Tests come first. Run `uv run pytest`, then `uvx ruff check`, then `uvx pyrefly check`.
- No ChatGPT adapter/downloader code in this plan.

## Review Focus

- **Stale save:** a second tab saving an old revision must get `409` and not erase the other tab's work. Tests: Task 4 `test_save_state_rejects_stale_revision`; Task 7 `test_state_put_and_conflict`.
- **Asset path traversal:** `/assets/../../etc/passwd` or an encoded variant must never serve outside `archive/`. Test: Task 7 `test_assets_served_and_traversal_rejected`.
- **Broken active path:** a conversation with a missing `current_leaf_message_uuid`, missing parent, or cycle must still produce pairs deterministically. Test: Task 1 `test_active_path_falls_back_to_index_order`.
- **Untrusted message HTML:** a message containing `<script>` or event-handler HTML must render inert. Test: Task 5 `test_render_markdown_strips_scripts`.
- **Missing pair after re-archive:** a collection item whose pair disappeared must remain stored, display as missing, and export with a placeholder rather than crash. Tests: Task 3 `test_reconcile_keeps_missing_item`; Task 6 `test_export_missing_pair_placeholder`.

## Pre-flight

- Start from a clean tree: commit or stash the existing file-download work (the 88-test baseline). Do not mix it with curator commits.
- Run `uv sync` and `uv run pytest -q`; the baseline must be green.
- Create/switch to a `feature/conversation-curator` branch.
- The plan assumes the archive already contains 348 conversations and the file/artifact/generated downloads from the previous task.

---

### Task 1: Curator model and pair extraction

**Files:**
- Create: `src/claude_data/curator/__init__.py`
- Create: `src/claude_data/curator/model.py`
- Create: `src/claude_data/curator/pairs.py`
- Test: `tests/test_curator_pairs.py`

**Interfaces:**
- Produces in `model.py`:
  - `Asset(asset_id: str, kind: str, label: str, local_path: str | None = None, remote_url: str | None = None, message_uuid: str | None = None, artifact_id: str | None = None, source_paths: tuple[str, ...] = ())`
  - `Message(external_id: str, parent_id: str | None, role: str, created_at: str, index: int, content: list[dict], attachments: list[dict], assets: list[Asset])`
  - `Source(source_id: str, provider: str, external_id: str, title: str, model: str, created_at: str, updated_at: str, raw_path: str, message_count: int, assets: list[Asset], path_fallback: bool = False)`
  - `Pair(pair_id: str, source_id: str, question_uuid: str, answer_uuids: list[str], date: str, question_text: str, answer_text: str, has_branches: bool, missing_answer: bool, assets: list[Asset])`
  - `CollectionItem(pair_id: str, source_id: str, question_uuid: str, date: str, preview: str, note: str = "")`
  - `Collection(collection_id: str, title: str, description: str, items: list[CollectionItem], created_at: str, updated_at: str)`
- Produces in `pairs.py`:
  - `PairBuild(pairs: list[Pair], messages: dict[str, list[Message]], path_fallback: bool)`
  - `message_text(message: Message) -> str`
  - `preview_text(message: Message, limit: int = 200) -> str`
  - `active_path(messages: list[Message], leaf_uuid: str | None) -> tuple[list[Message], bool]`
  - `build_pairs(source: Source, messages: list[Message], leaf_uuid: str | None) -> PairBuild`

- [ ] **Step 1: Write the failing tests**

```python
from claude_data.curator.model import Message, Source
from claude_data.curator.pairs import active_path, build_pairs, message_text, preview_text


def msg(uuid, sender, index, parent=None, text="", created="2026-01-01T00:00:00Z"):
    return Message(
        external_id=uuid,
        parent_id=parent,
        role=sender,
        created_at=created,
        index=index,
        content=[{"type": "text", "text": text}],
        attachments=[],
        assets=[],
    )


def source(message_count=4):
    return Source(
        source_id="claude:conv-1",
        provider="claude",
        external_id="conv-1",
        title="T",
        model="m",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        raw_path="conversations/conv-1.json",
        message_count=message_count,
        assets=[],
    )


def test_active_path_walks_leaf_to_root():
    messages = [
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
        msg("h2", "human", 2, parent="a1"),
        msg("a2", "assistant", 3, parent="h2"),
    ]
    path, fallback = active_path(messages, "a2")
    assert [m.external_id for m in path] == ["h1", "a1", "h2", "a2"]
    assert fallback is False


def test_active_path_treats_root_sentinel_as_end():
    messages = [
        msg("h1", "human", 0, parent="00000000-0000-4000-8000-000000000000"),
        msg("a1", "assistant", 1, parent="h1"),
    ]
    path, fallback = active_path(messages, "a1")
    assert [m.external_id for m in path] == ["h1", "a1"]
    assert fallback is False


def test_active_path_falls_back_to_index_order():
    messages = [
        msg("h2", "human", 2),
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
    ]
    path, fallback = active_path(messages, "missing-leaf")
    assert [m.external_id for m in path] == ["h1", "a1", "h2"]
    assert fallback is True


def test_build_pairs_groups_answers_and_marks_missing():
    messages = [
        msg("h1", "human", 0, text="q1"),
        msg("a1", "assistant", 1, parent="h1", text="a1"),
        msg("a1b", "assistant", 2, parent="h1", text="a1b"),
        msg("h2", "human", 3, parent="a1b", text="q2"),
    ]
    build = build_pairs(source(4), messages, "h2")
    assert [p.pair_id for p in build.pairs] == ["claude:conv-1:h1", "claude:conv-1:h2"]
    assert build.pairs[0].answer_uuids == ["a1", "a1b"]
    assert build.pairs[0].question_text == "q1"
    assert build.pairs[1].missing_answer is True


def test_build_pairs_marks_branch():
    messages = [
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
        msg("a2", "assistant", 2, parent="h1"),
        msg("h2", "human", 3, parent="a1"),
    ]
    build = build_pairs(source(4), messages, "h2")
    assert build.pairs[0].has_branches is True


def test_preview_text_joins_text_blocks_and_truncates():
    message = Message(
        external_id="h1",
        parent_id=None,
        role="human",
        created_at="2026-01-01T00:00:00Z",
        index=0,
        content=[{"type": "text", "text": "hello"}, {"type": "thinking", "thinking": "x"}],
        attachments=[],
        assets=[],
    )
    assert message_text(message) == "hello"
    assert preview_text(message, limit=5) == "hello"
    assert preview_text(message, limit=2) == "he"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_pairs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_data.curator'`.

- [ ] **Step 3: Implement the model and pair builder**

Implement `model.py` with the six dataclasses above. Implement `pairs.py`:

- `active_path`: build a UUID map; if `leaf_uuid` exists, walk `parent_id` to the root, reverse, and return `(path, False)`; stop without fallback at `parent_id` `None`, empty, or the root sentinel `00000000-0000-4000-8000-000000000000`. On a missing non-root parent or cycle, fall back to the full `(index, created_at, external_id)` order with `True`. If the leaf is missing, return that same index order with `True`.
- `build_pairs`: walk the path; a `human` message starts a pair, following `assistant` messages are answers, and the next `human` closes the previous pair. Use the human message `created_at` as the pair date; if it is empty, use `source.created_at`; if that is also empty, use `1970-01-01T00:00:00Z`. Mark `missing_answer = True` when a pair has no answers. Compute `has_branches` by counting children by `parent_id` and checking whether any message in the pair has more than one child. Build `pair_id` as `f"{source.source_id}:{question.external_id}"`. Aggregate assets by `asset_id`. Store each pair's messages in the returned map.
- `message_text`: join `content` blocks whose `type == "text"` with spaces and trim, without truncation.
- `preview_text`: return `message_text(message)[:limit]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_pairs.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/__init__.py src/claude_data/curator/model.py src/claude_data/curator/pairs.py tests/test_curator_pairs.py
git commit -m "feat: add curator model and pair extraction"
```

---

### Task 2: Claude adapter and asset normalization

**Files:**
- Create: `src/claude_data/curator/adapters.py`
- Create: `tests/curator_fixtures.py`
- Test: `tests/test_curator_adapter.py`

**Interfaces:**
- Produces in `adapters.py`:
  - `SourceBundle(source: Source, messages: list[Message], leaf_uuid: str | None)`
  - `SourceAdapter` protocol with `provider: str` and `load(path: Path, archive_root: Path) -> SourceBundle`
  - `ClaudeAdapter(provider: str = "claude")` with `load(path: Path, archive_root: Path) -> SourceBundle`
- Asset IDs must be stable:
  - files: `file:{source_id}:{file_uuid}`
  - media: `media:{source_id}:{sha1(url)[:12]}`
  - artifacts: `artifact:{source_id}:{artifact_id}`
  - generated zips: `generated:{source_id}:{local_path}`

- [ ] **Step 1: Write the failing tests**

Add a small fixture helper in `tests/curator_fixtures.py`:

```python
import json
from pathlib import Path


def message(uuid, sender, index, parent="00000000-0000-4000-8000-000000000000", text="", created="2026-01-01T00:00:00Z"):
    return {
        "uuid": uuid,
        "sender": sender,
        "index": str(index),
        "parent_message_uuid": parent,
        "created_at": created,
        "updated_at": created,
        "text": "",
        "content": [{"type": "text", "text": text}],
        "attachments": [],
        "files": [],
        "truncated": False,
        "sync_sources": [],
    }


def write_conversation(root: Path, payload: dict, meta: dict | None = None) -> Path:
    conversations = root / "conversations"
    conversations.mkdir(parents=True, exist_ok=True)
    path = conversations / f"{payload['uuid']}.json"
    path.write_text(json.dumps(payload))
    if meta is not None:
        (conversations / f"{payload['uuid']}.meta.json").write_text(json.dumps(meta))
    return path
```

Test file:

```python
from claude_data.curator.adapters import ClaudeAdapter
from curator_fixtures import message, write_conversation


def test_load_builds_source_and_messages(tmp_path):
    payload = {
        "uuid": "conv-1",
        "name": "My conversation",
        "model": "claude",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            message("h1", "human", 0, text="q"),
            message("a1", "assistant", 1, parent="h1", text="a"),
        ],
    }
    path = write_conversation(tmp_path, payload)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert bundle.source.source_id == "claude:conv-1"
    assert bundle.source.title == "My conversation"
    assert bundle.leaf_uuid == "a1"
    assert [m.role for m in bundle.messages] == ["human", "assistant"]


def test_asset_normalization_covers_all_kinds(tmp_path):
    (tmp_path / "files" / "conv-1").mkdir(parents=True)
    (tmp_path / "files" / "conv-1" / "photo.webp").write_bytes(b"x")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "paper.pdf").write_bytes(b"x")
    (tmp_path / "artifacts" / "conv-1").mkdir(parents=True)
    (tmp_path / "artifacts" / "conv-1" / "demo.py").write_text("print(1)")
    (tmp_path / "generated" / "conv-1").mkdir(parents=True)
    (tmp_path / "generated" / "conv-1" / "files-1.zip").write_bytes(b"x")

    payload = {
        "uuid": "conv-1",
        "name": "T",
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            {**message("h1", "human", 0, text="see https://example.com/a.pdf"), "files": [
                {"file_uuid": "f1", "file_name": "photo.png", "file_kind": "image", "local_path": "files/conv-1/photo.webp"}
            ]},
            {**message("a1", "assistant", 1, parent="h1"), "content": [
                {"type": "tool_use", "name": "artifacts", "input": {"id": "demo", "title": "Demo"}},
                {"type": "tool_result", "name": "present_files", "content": [
                    {"type": "local_resource", "file_path": "/out/report.txt"}
                ]},
            ]},
        ],
    }
    meta = {
        "media": {"https://example.com/a.pdf": "media/paper.pdf"},
        "artifacts": {"demo": {"path": "artifacts/conv-1/demo.py"}},
        "generated": [{"path": "generated/conv-1/files-1.zip", "source_paths": ["/out/report.txt"]}],
    }
    path = write_conversation(tmp_path, payload, meta)
    bundle = ClaudeAdapter().load(path, tmp_path)
    kinds = {a.kind for m in bundle.messages for a in m.assets}
    assert kinds == {"image", "media", "artifact", "generated"}
    assert bundle.source.assets == []


def test_unmatched_generated_zip_is_source_level(tmp_path):
    (tmp_path / "generated" / "conv-1").mkdir(parents=True)
    (tmp_path / "generated" / "conv-1" / "files-1.zip").write_bytes(b"x")
    payload = {
        "uuid": "conv-1",
        "chat_messages": [message("h1", "human", 0)],
        "current_leaf_message_uuid": "h1",
    }
    meta = {"generated": [{"path": "generated/conv-1/files-1.zip", "source_paths": ["/out/missing.txt"]}]}
    path = write_conversation(tmp_path, payload, meta)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert [a.kind for a in bundle.source.assets] == ["generated"]


def test_missing_meta_is_tolerated(tmp_path):
    payload = {"uuid": "conv-1", "chat_messages": [message("h1", "human", 0)], "current_leaf_message_uuid": "h1"}
    path = write_conversation(tmp_path, payload)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert bundle.messages[0].assets == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_data.curator.adapters'`.

- [ ] **Step 3: Implement `ClaudeAdapter`**

Read the conversation JSON and optional `.meta.json`. Build `Source` from the payload fields, with `raw_path` relative to `archive_root` and `message_count = len(chat_messages)`. Build `Message` objects from `chat_messages`, mapping `sender` to `role`, parsing `index` as an integer, and preserving `content`/`attachments`. Resolve assets:

- `files[]` with `local_path` → `image` if `file_kind == "image"`, else `file`.
- Citation URLs found in text that match `meta["media"]` → `media`.
- `tool_use` named `artifacts` → `artifact`, using `meta["artifacts"][id]["path"]`.
- `present_files` `local_resource.file_path` → `generated`, matching `meta["generated"][*]["source_paths"]`; track used generated entries, and return unused entries as `Source.assets`.
- Keep assets with `local_path=None` when the URL/metadata exists but the file is absent.

Use `dataclasses.replace` to attach the resolved asset list to each `Message`. Return `SourceBundle(source, messages, payload.get("current_leaf_message_uuid"))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_adapter.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/adapters.py tests/curator_fixtures.py tests/test_curator_adapter.py
git commit -m "feat: normalize Claude conversations and assets"
```

---

### Task 3: Library aggregation and reconciliation

**Files:**
- Create: `src/claude_data/curator/library.py`
- Test: `tests/test_curator_library.py`

**Interfaces:**
- Produces in `library.py`:
  - `Library(archive_root: Path, sources: list[Source], pairs: list[Pair], pair_messages: dict[str, list[Message]])`
  - `Library.build(archive_root: Path) -> Library`
  - `Library.to_json() -> dict`
  - `Library.pair_content(pair_id: str) -> dict`
  - `Library.reconcile(collections: list[Collection]) -> list[Collection]`

- [ ] **Step 1: Write the failing tests**

```python
import json

from claude_data.curator.library import Library
from claude_data.curator.model import Collection, CollectionItem
from curator_fixtures import message, write_conversation


def item(pair_id, source_id, question_uuid, date, preview):
    return CollectionItem(pair_id=pair_id, source_id=source_id, question_uuid=question_uuid, date=date, preview=preview)


def make_collection(*items):
    return Collection(
        collection_id="c1",
        title="Topic",
        description="",
        items=list(items),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def make_library(tmp_path):
    for conv, uuid, date in [("conv-1", "h1", "2026-01-02T00:00:00Z"), ("conv-2", "h2", "2026-01-01T00:00:00Z")]:
        payload = {
            "uuid": conv,
            "name": conv,
            "model": "m",
            "created_at": date,
            "updated_at": date,
            "current_leaf_message_uuid": uuid,
            "chat_messages": [
                message(uuid, "human", 0, text="q", created=date),
                message(f"{uuid}-a", "assistant", 1, parent=uuid, text="a", created=date),
            ],
        }
        write_conversation(tmp_path, payload)
    return Library.build(tmp_path)


def test_build_orders_pairs_by_date(tmp_path):
    library = make_library(tmp_path)
    assert [p.source_id for p in library.pairs] == ["claude:conv-2", "claude:conv-1"]


def test_pair_content_returns_question_and_answers(tmp_path):
    library = make_library(tmp_path)
    pair = library.pairs[0]
    content = library.pair_content(pair.pair_id)
    assert content["pair"]["pair_id"] == pair.pair_id
    assert content["question"]["external_id"] == pair.question_uuid
    assert content["answers"][0]["role"] == "assistant"


def test_to_json_is_serializable(tmp_path):
    library = make_library(tmp_path)
    payload = library.to_json()
    json.dumps(payload)
    assert {"sources", "pairs"} <= set(payload)


def test_reconcile_relinks_by_question_uuid(tmp_path):
    library = make_library(tmp_path)
    old = library.pairs[0]
    collection = make_collection(item(pair_id="missing", source_id=old.source_id, question_uuid=old.question_uuid, date=old.date, preview=old.question_text))
    reconciled = library.reconcile([collection])
    assert reconciled[0].items[0].pair_id == old.pair_id


def test_reconcile_keeps_missing_item(tmp_path):
    library = make_library(tmp_path)
    collection = make_collection(item(pair_id="gone", source_id="claude:gone", question_uuid="gone", date="2026-01-01T00:00:00Z", preview="gone"))
    reconciled = library.reconcile([collection])
    assert reconciled[0].items[0].pair_id == "gone"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_data.curator.library'`.

- [ ] **Step 3: Implement `Library`**

Iterate `archive_root/conversations/*.json`, skipping `*.meta.json`. Load each with `ClaudeAdapter`, call `build_pairs`, replace the source's `path_fallback` from `PairBuild`, and aggregate. Sort sources by `(created_at, source_id)` and pairs by `(date, pair_id)`. Build `source_by_id` and `pair_by_id` in `__post_init__`.

`to_json` returns `{"sources": [asdict(s) ...], "pairs": [asdict(p) ...]}`. `pair_content` returns `{"pair": asdict(pair), "question": asdict(messages[0]), "answers": [asdict(m) for m in messages[1:]]}` and raises `KeyError` for an unknown pair.

`reconcile` returns copies of collections with items re-linked by exact `pair_id`, then `source_id + question_uuid`, then `source_id + date + preview prefix`; unresolved items keep their original fields. Do not write anything.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_library.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/library.py tests/test_curator_library.py
git commit -m "feat: aggregate curator library and reconcile items"
```

---

### Task 4: Collections and revisioned persistence

**Files:**
- Modify: `src/claude_data/curator/model.py` (add `State`)
- Create: `src/claude_data/curator/store.py`
- Test: `tests/test_curator_store.py`

**Interfaces:**
- Produces in `model.py`: `State(revision: int, collections: list[Collection])`
- Produces in `store.py`:
  - `StoreError(ValueError)`
  - `RevisionConflict(RuntimeError)`
  - `load_state(data_dir: Path) -> State`
  - `save_state(data_dir: Path, expected_revision: int, collections: list[Collection]) -> State`
  - `validate_collections(collections: list[Collection]) -> None`

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from claude_data.curator.model import Collection, CollectionItem
from claude_data.curator.store import (
    RevisionConflict,
    StoreError,
    load_state,
    save_state,
    validate_collections,
)


def coll(cid="c1", title="Topic"):
    return Collection(
        collection_id=cid,
        title=title,
        description="",
        items=[CollectionItem(pair_id="p1", source_id="claude:conv-1", question_uuid="h1", date="2026-01-01T00:00:00Z", preview="q")],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_load_missing_state_returns_empty(tmp_path):
    state = load_state(tmp_path)
    assert state.revision == 0 and state.collections == []


def test_save_increments_revision_and_writes_backup(tmp_path):
    first = save_state(tmp_path, 0, [coll()])
    assert first.revision == 1
    second = save_state(tmp_path, 1, [coll(title="Changed")])
    assert second.revision == 2
    assert json.loads((tmp_path / "collections.json.bak").read_text())["revision"] == 1


def test_save_state_rejects_stale_revision(tmp_path):
    save_state(tmp_path, 0, [coll()])
    with pytest.raises(RevisionConflict):
        save_state(tmp_path, 0, [coll(title="Stale")])


def test_validate_rejects_duplicate_ids_and_empty_title():
    with pytest.raises(ValueError):
        validate_collections([coll(), coll()])
    with pytest.raises(ValueError):
        validate_collections([coll(title="")])


def test_validate_allows_unresolved_pair():
    validate_collections([coll()])


def test_malformed_state_raises_store_error(tmp_path):
    (tmp_path / "collections.json").write_text("{not json")
    with pytest.raises(StoreError):
        load_state(tmp_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_store.py -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError` for `claude_data.curator.store`.

- [ ] **Step 3: Implement the store**

`load_state` returns `State(0, [])` when the file is absent; otherwise parse `{"version": 1, "revision": N, "collections": [...]}` and validate. Malformed JSON or shape raises `StoreError` with a clear message. `save_state` creates `data_dir`, acquires an `fcntl.flock` on `data_dir/.lock`, reloads the current state, compares revisions, validates the new collections, copies the old `collections.json` to `collections.json.bak`, writes a temp file and `os.replace`s it, and returns the new `State(revision=current+1, ...)`.

`validate_collections` rejects duplicate collection IDs, empty titles, duplicate item pair IDs within one collection, and missing required item fields. It does **not** require pair IDs to exist in the library.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_store.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/model.py src/claude_data/curator/store.py tests/test_curator_store.py
git commit -m "feat: persist revisioned curator collections"
```

---

### Task 5: Markdown rendering and sanitization

**Files:**
- Create: `src/claude_data/curator/render.py`
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/test_curator_render.py`

**Interfaces:**
- Produces:
  - `render_markdown(text: str, link_prefix: str = "/assets/") -> str`
  - `render_message(message: Message, link_prefix: str = "/assets/") -> str`
  - `render_pair(pair: Pair, question: Message, answers: list[Message], link_prefix: str = "/assets/") -> dict`
- `render_pair` returns `{"question_html": str, "answer_html": str, "assets": list[dict]}`.

- [ ] **Step 1: Add dependencies**

Run: `uv add markdown bleach`
Expected: `pyproject.toml` and `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

```python
from claude_data.curator.model import Asset, Message, Pair
from claude_data.curator.render import render_markdown, render_message, render_pair


def msg(uuid, role, content):
    return Message(
        external_id=uuid,
        parent_id=None,
        role=role,
        created_at="2026-01-01T00:00:00Z",
        index=0,
        content=content,
        attachments=[],
        assets=[],
    )


def test_render_markdown_strips_scripts():
    html = render_markdown("<script>alert(1)</script> **bold**")
    assert "<script>" not in html
    assert "<strong>bold</strong>" in html


def test_render_markdown_rewrites_local_assets():
    html = render_markdown("![x](media/a.png)")
    assert 'src="/assets/media/a.png"' in html


def test_render_message_escapes_tool_input():
    html = render_message(msg("a1", "assistant", [
        {"type": "tool_use", "name": "bash", "input": {"command": "<script>x</script>"}}
    ]))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_pair_returns_assets():
    asset = Asset(asset_id="a1", kind="image", label="x", local_path="files/x.webp")
    question = msg("h1", "human", [{"type": "text", "text": "q"}])
    answer = msg("a1", "assistant", [{"type": "text", "text": "a"}])
    pair = Pair("p1", "claude:conv-1", "h1", ["a1"], "2026-01-01T00:00:00Z", "q", "a", False, False, [asset])
    rendered = render_pair(pair, question, [answer])
    assert rendered["assets"][0]["kind"] == "image"
    assert "q" in rendered["question_html"]
```

- [ ] **Step 3: Implement rendering**

Rewrite local Markdown targets beginning with `media/`, `files/`, `artifacts/`, or `generated/` to `link_prefix + target`. Convert with `markdown.markdown(text, extensions=["fenced_code", "tables"])`. Sanitize with `bleach.clean` allowing common formatting, tables, links, images, `details`, and `summary`; allow `href`, `src`, `alt`, `title`, and `class` attributes. Allow `http`, `https`, and `mailto` protocols.

`render_message` renders `text` blocks as Markdown; renders `thinking`, `tool_use`, and `tool_result` as `<details><summary>...</summary><pre>escaped text</pre></details>`; skips unknown blocks or renders escaped JSON.

`render_pair` renders the question and answers and returns the pair's assets as `asdict` dictionaries.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_render.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/claude_data/curator/render.py tests/test_curator_render.py
git commit -m "feat: render sanitized curator content"
```

---

### Task 6: Self-contained export bundle

**Files:**
- Create: `src/claude_data/curator/export.py`
- Test: `tests/test_curator_export.py`

**Interfaces:**
- Produces:
  - `ExportResult(folder: Path, zip_path: Path)`
  - `export_collection(collection: Collection, library: Library, archive_root: Path, out_dir: Path) -> ExportResult`
  - `build_export_zip(folder: Path) -> Path`
  - `copy_assets(assets: list[Asset], archive_root: Path, assets_dir: Path) -> tuple[dict[str, str], list[str]]`
  - `rewrite_links(text: str, mapping: dict[str, str]) -> str`

- [ ] **Step 1: Write the failing tests**

Add these fixtures to `tests/conftest.py` (they are reused by the server tests):

```python
import pytest

from claude_data.curator.library import Library
from claude_data.curator.model import Collection, CollectionItem
from curator_fixtures import message, write_conversation


@pytest.fixture
def curator_archive(tmp_path):
    archive = tmp_path / "archive"
    (archive / "files" / "conv-1").mkdir(parents=True)
    (archive / "files" / "conv-1" / "photo.webp").write_bytes(b"image")
    payload = {
        "uuid": "conv-1",
        "name": "T",
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            {**message("h1", "human", 0, text="q"), "files": [
                {"file_uuid": "f1", "file_name": "photo.png", "file_kind": "image", "local_path": "files/conv-1/photo.webp"}
            ]},
            message("a1", "assistant", 1, parent="h1", text="a"),
        ],
    }
    write_conversation(archive, payload)
    return archive


@pytest.fixture
def curator_library(curator_archive):
    return Library.build(curator_archive)


@pytest.fixture
def curator_collection(curator_library):
    pair = curator_library.pairs[0]
    return Collection(
        collection_id="c1",
        title="Topic",
        description="",
        items=[CollectionItem(pair_id=pair.pair_id, source_id=pair.source_id, question_uuid=pair.question_uuid, date=pair.date, preview=pair.question_text)],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
```

Tests:

```python
import zipfile

from claude_data.curator.export import copy_assets, export_collection, rewrite_links
from claude_data.curator.model import Collection, CollectionItem


def test_export_writes_bundle_and_copies_assets(tmp_path, curator_archive, curator_library, curator_collection):
    result = export_collection(curator_collection, curator_library, curator_archive, tmp_path / "exports")
    assert (result.folder / "collection.md").is_file()
    assert (result.folder / "collection.html").is_file()
    assert (result.folder / "collection.json").is_file()
    assert list((result.folder / "assets").iterdir())
    assert "2026-01-01" in (result.folder / "collection.md").read_text()
    assert "assets/" in (result.folder / "collection.md").read_text()


def test_export_zip_contains_bundle(tmp_path, curator_archive, curator_library, curator_collection):
    result = export_collection(curator_collection, curator_library, curator_archive, tmp_path / "exports")
    with zipfile.ZipFile(result.zip_path) as archive:
        names = archive.namelist()
    assert any(name.endswith("collection.md") for name in names)
    assert any("/assets/" in name for name in names)


def test_export_missing_pair_placeholder(tmp_path, curator_archive, curator_library):
    missing = Collection(
        collection_id="c2", title="Missing", description="",
        items=[CollectionItem(pair_id="gone", source_id="claude:gone", question_uuid="gone", date="2026-01-01T00:00:00Z", preview="lost question")],
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    result = export_collection(missing, curator_library, curator_archive, tmp_path / "exports")
    assert "missing" in (result.folder / "collection.md").read_text().lower()


def test_copy_assets_dedupes_by_hash(tmp_path, curator_archive):
    from claude_data.curator.model import Asset
    first = Asset("a1", "image", "one", local_path="files/conv-1/photo.webp")
    second = Asset("a2", "image", "two", local_path="files/conv-1/photo.webp")
    mapping, missing = copy_assets([first, second], curator_archive, tmp_path / "assets")
    assert len(set(mapping.values())) == 1
    assert missing == []


def test_rewrite_links_uses_export_paths():
    assert rewrite_links("![x](media/a.png)", {"media/a.png": "assets/abc-a.png"}) == "![x](assets/abc-a.png)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_data.curator.export'`.

- [ ] **Step 3: Implement export**

For each collection item, resolve the pair and its assets; also include the source-level assets for that item's source, deduplicated by `asset_id`. `copy_assets` copies existing `asset.local_path` files under `archive_root` into `assets/` with a content-hash prefix and a sanitized label, dedupes by hash, returns `(asset_id -> relative path, missing asset ids)`, and never writes outside `assets_dir`. `rewrite_links` replaces each source local path in a Markdown/HTML string with its exported asset path. Missing assets are skipped and noted. For `collection.md`, use `message_text` for full question/answer text and `rewrite_links`; for `collection.html`, use `render_pair` with the `assets/` prefix. A missing pair writes a `## <date> — missing source` section with the snapshot preview. `collection.json` contains the collection and resolved pair snapshots. Zip the folder with `zipfile` and return both paths. Use a temporary directory and `os.replace` for the final folder so a failed export does not leave a half-written bundle.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_export.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/export.py tests/test_curator_export.py
git commit -m "feat: export self-contained collection bundles"
```

---

### Task 7: Local HTTP server and JSON API

**Files:**
- Create: `src/claude_data/curator/server.py`
- Create: `src/claude_data/curator/static/index.html` (placeholder this task)
- Test: `tests/test_curator_server.py`

**Interfaces:**
- Produces:
  - `create_server(archive_root: Path, data_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer`
  - `serve(archive_root: Path, data_dir: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None`
- Server attributes: `library: Library`, `archive_root: Path`, `data_dir: Path`, `static_dir: Path`.
- Endpoints: `GET /api/library`, `GET /api/pairs/{pair_id}`, `GET /api/state`, `PUT /api/state`, `POST /api/reload`, `GET /api/export/{collection_id}.zip`, `GET /assets/...`, `GET /` (index), static files.
- `GET /api/pairs/{pair_id}` returns `{"pair": ..., "question_html": ..., "answer_html": ..., "assets": [...]}`.
- `GET /api/state` returns `{"revision": N, "collections": library.reconcile(state.collections)}`.
- `PUT /api/state` accepts `{"expected_revision": N, "collections": [...]}` and returns `{"revision": N+1, "collections": [...]}`.

- [ ] **Step 1: Write the failing tests**

Use the `curator_archive` fixture from `tests/conftest.py` and a live server on port 0:

```python
import json
import threading
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import quote

import pytest

from claude_data.curator.server import create_server


def get_json(url):
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def put_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


@pytest.fixture
def server(curator_archive, tmp_path):
    srv = create_server(curator_archive, tmp_path / "curated", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_library_endpoint(server):
    _, url = server
    data = get_json(f"{url}/api/library")
    assert data["pairs"][0]["pair_id"].startswith("claude:conv-1:")


def test_pair_endpoint(server):
    _, url = server
    pair_id = get_json(f"{url}/api/library")["pairs"][0]["pair_id"]
    data = get_json(f"{url}/api/pairs/{quote(pair_id)}")
    assert "question_html" in data and "answer_html" in data


def test_state_put_and_conflict(server, curator_collection):
    srv, url = server
    state = get_json(f"{url}/api/state")
    payload = {"expected_revision": state["revision"], "collections": [asdict(curator_collection)]}
    saved = put_json(f"{url}/api/state", payload)
    assert saved["revision"] == state["revision"] + 1
    with pytest.raises(urllib.error.HTTPError) as exc:
        put_json(f"{url}/api/state", payload)
    assert exc.value.code == 409


def test_assets_served_and_traversal_rejected(server, curator_archive, tmp_path):
    _, url = server
    with urllib.request.urlopen(f"{url}/assets/files/conv-1/photo.webp") as response:
        assert response.read() == b"image"
    (tmp_path / "secret.txt").write_text("secret")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/assets/%2e%2e/secret.txt")
    assert exc.value.code == 404


def test_export_zip_endpoint(server, curator_collection):
    _, url = server
    state = get_json(f"{url}/api/state")
    put_json(f"{url}/api/state", {"expected_revision": state["revision"], "collections": [asdict(curator_collection)]})
    with urllib.request.urlopen(f"{url}/api/export/c1.zip") as response:
        assert response.headers["Content-Type"] == "application/zip"
        assert response.read()[:2] == b"PK"


def test_reload_endpoint(server):
    srv, url = server
    request = urllib.request.Request(f"{url}/api/reload", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        assert json.load(response)["pairs"] >= 1


def test_unknown_path_404(server):
    _, url = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/api/nope")
    assert exc.value.code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curator_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_data.curator.server'`.

- [ ] **Step 3: Implement the server**

Use `ThreadingHTTPServer`/`BaseHTTPRequestHandler`. On creation, build `Library` and store paths. Route as above. Pair content uses `library.pair_content`, `render_pair`, and `/assets/` link prefix. State uses `load_state`/`save_state`; deserialize request `collections` into `Collection`/`CollectionItem` before validation, return `400` on malformed shapes, `409` on `RevisionConflict`. Asset serving resolves `archive_root / unquoted_path` and rejects anything not `is_relative_to(archive_root)`. Static files are served from `static_dir`; `/` maps to `index.html`. The export endpoint loads the collection from state, exports to `data_dir/exports`, and streams the zip. `POST /api/reload` rebuilds `server.library` and returns `{"sources": N, "pairs": M}`. `log_message` stays quiet or minimal. `serve` prints the URL, optionally opens the browser, serves until `KeyboardInterrupt`, then shuts down.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_curator_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/server.py src/claude_data/curator/static/index.html tests/test_curator_server.py
git commit -m "feat: serve curator library and collections API"
```

---

### Task 8: CLI `view` command

**Files:**
- Modify: `src/claude_data/__init__.py`
- Modify: `tests/test_cli.py`
- Test: `tests/test_view_cli.py` (or extend `tests/test_cli.py`)

**Interfaces:**
- `parse_args` gains:
  - positional `command` (`nargs="?"`, `choices=["view"]`, default `None`)
  - `--port` (`int`, default `None`)
  - `--archive` (`Path`, default `None`)
  - `--data-dir` (`Path`, default `None`)
  - `--no-browser` (`store_true`, default `False`)
- `main()` dispatches `view` to `claude_data.curator.server.serve(archive_root=args.archive or ARCHIVE_ROOT, data_dir=args.data_dir or Path("curated"), port=args.port or 8765, open_browser=not args.no_browser)`.

- [ ] **Step 1: Write the failing tests**

```python
import sys

import pytest

from claude_data import main, parse_args


def test_parse_view_defaults():
    ns = parse_args(["view"])
    assert ns.command == "view"
    assert ns.port is None and ns.archive is None and ns.data_dir is None
    assert ns.no_browser is False
    assert ns.show_browser is False


def test_parse_view_options():
    ns = parse_args(["view", "--port", "9000", "--archive", "a", "--data-dir", "d", "--no-browser"])
    assert (ns.port, str(ns.archive), str(ns.data_dir), ns.no_browser) == (9000, "a", "d", True)


def test_view_rejects_archive_flags():
    with pytest.raises(SystemExit):
        parse_args(["view", "--refresh"])


def test_archive_rejects_view_flags():
    with pytest.raises(SystemExit):
        parse_args(["--port", "9000"])


def test_main_dispatches_view(monkeypatch):
    calls = {}
    monkeypatch.setattr("claude_data.curator.server.serve", lambda **kw: calls.update(kw))
    monkeypatch.setattr(sys, "argv", ["claude-data", "view", "--no-browser", "--port", "9000"])
    main()
    assert calls["port"] == 9000 and calls["open_browser"] is False
```

Update existing tests to assert `parse_args([]).command is None`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_view_cli.py -v`
Expected: FAIL for missing arguments/dispatch.

- [ ] **Step 3: Implement the CLI changes**

Add the positional/flags. Reject archive flags (`--refresh`, `--download-files`, `--export-artifacts`, `--conversation`, `--max-size-mb`, `--force`, `--dry-run`, `--login`, `--headed`, `--show-browser`) when `command == "view"`. Reject view flags (`--port`, `--archive`, `--data-dir`, `--no-browser`) when `command != "view"`. Keep existing archive/file-mode behavior unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_view_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/__init__.py tests/test_cli.py tests/test_view_cli.py
git commit -m "feat: add curator view command"
```

---

### Task 9: Read-only frontend

**Files:**
- Create: `src/claude_data/curator/static/index.html`
- Create: `src/claude_data/curator/static/style.css`
- Create: `src/claude_data/curator/static/app.js`

**Interfaces:**
- Consumes `GET /api/library`, `GET /api/state`, `GET /api/pairs/{pair_id}`.
- DOM IDs: `#library`, `#collections`, `#preview`, `#search`, `#source-filter`, `#date-from`, `#date-to`, `#reload`, `#save-status`.

- [ ] **Step 1: Build the static shell**

Create a three-pane layout: library left, collections center, preview right; top bar with search, source filter, date range, reload, and save status. Use semantic HTML and CSS grid; no external fonts or CDNs.

- [ ] **Step 2: Implement library loading and filtering**

`app.js` fetches `/api/library` and `/api/state`, stores them in a module-level `state`, renders source/pair rows with date, provider badge, conversation title, preview, and branch/missing badges. Filters update the visible list. The collection pane renders existing collections read-only. Use `textContent` for labels and only server-sanitized `question_html`/`answer_html` via `innerHTML`.

- [ ] **Step 3: Implement pair preview**

On pair selection, fetch `/api/pairs/{pair_id}` and render `question_html`, `answer_html`, and asset links. Dates show local time with a UTC tooltip. Show a clear empty state when nothing is selected.

- [ ] **Step 4: Manual verification**

Run `uv run claude-data view --no-browser --port 8765` in one terminal; open `http://127.0.0.1:8765/` and verify:

- library rows show dates, sources, previews;
- filters narrow the list;
- selecting a pair shows full Markdown/code/images;
- no console errors;
- the page works at a laptop width.

- [ ] **Step 5: Commit**

```bash
git add src/claude_data/curator/static/index.html src/claude_data/curator/static/style.css src/claude_data/curator/static/app.js
git commit -m "feat: add read-only curator frontend"
```

---

### Task 10: Collection editing frontend

**Files:**
- Modify: `src/claude_data/curator/static/index.html`
- Modify: `src/claude_data/curator/static/style.css`
- Modify: `src/claude_data/curator/static/app.js`

**Interfaces:**
- Consumes `PUT /api/state` with `{expected_revision, collections}`.
- Produces UI actions: create/rename/delete collection, add pairs, remove item, reorder, split, merge, sort by date, export.

- [ ] **Step 1: Add collection state and save path**

Load `revision` and `collections` from `/api/state`. Add `saveState()` with a ~400ms debounce and a visible save status. On `409`, show a non-blocking banner offering reload; do not overwrite.

- [ ] **Step 2: Add collection actions**

Implement create, rename, delete, add selected pairs, remove item, move up/down, split here (tail to a new collection), merge collections (append and remove the source after confirmation), and sort by date (one-shot rewrite). Creating an item snapshots `pair_id`, `source_id`, `question_uuid`, `date`, and `preview`.

- [ ] **Step 3: Add drag and drop**

Make library rows draggable; accept drops on collection items and the collection list. Reorder within a collection; append when dropped on the collection. Keep move-up/down buttons as the keyboard-accessible fallback. Dropping while a search filter is active must not duplicate items.

- [ ] **Step 4: Add export**

Add an export button that navigates to `/api/export/{collection_id}.zip` and warns when the collection contains large assets.

- [ ] **Step 5: Manual verification**

Run the server and verify:

- create a collection and add pairs from two conversations;
- drag to reorder and reload the page to confirm persistence;
- remove, split, merge, and date-sort;
- open a second tab and save from both; the stale tab shows the conflict banner and does not erase the first tab's changes;
- export produces a zip containing `collection.md`, `collection.html`, `collection.json`, and assets.

- [ ] **Step 6: Commit**

```bash
git add src/claude_data/curator/static/index.html src/claude_data/curator/static/style.css src/claude_data/curator/static/app.js
git commit -m "feat: add curator collection editing UI"
```

---

### Task 11: Integration, docs, and final verification

**Files:**
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-10-25-conversation-curator-design.md` (status → Approved)
- Test: full suite

- [ ] **Step 1: Ignore curated user data**

Add `curated/` to `.gitignore`. Do not ignore the spec or plan.

- [ ] **Step 2: Update AGENTS.md**

Add:

- `uv run claude-data view [--port 8765] [--archive archive] [--data-dir curated] [--no-browser]`
- `src/claude_data/curator/` modules: model, pairs, adapters, library, store, render, export, server, static.
- Raw archive immutable; collections live in `curated/collections.json` with revision/backup; `curated/` is user data.
- Local-only server on 127.0.0.1; no external network.
- Test count and new test files.

- [ ] **Step 3: Run full verification**

```bash
uv run pytest -q
uvx ruff check
uvx pyrefly check
```

Expected: all tests pass, ruff clean, pyrefly 0 errors.

- [ ] **Step 4: Smoke-test against the real archive**

Run `uv run claude-data view --no-browser --port 8765`; open the URL; create a temporary collection with pairs from two conversations, reorder, export, and confirm the zip. Remove the temporary collection afterward.

- [ ] **Step 5: Commit**

```bash
git add .gitignore AGENTS.md docs/superpowers/specs/2026-10-25-conversation-curator-design.md
git commit -m "docs: document conversation curator"
```

---

## Plan Self-Review

- **Spec coverage:** model/pairs (Task 1), assets/adapter (Task 2), library/reconcile (Task 3), persistence/revision (Task 4), rendering (Task 5), export (Task 6), server/API (Task 7), CLI (Task 8), read-only UI (Task 9), editing UI (Task 10), docs/verification (Task 11).
- **Deliberate non-goals:** ChatGPT adapter, message text editing, import/restore UI, multi-user/auth, auto-clustering.
- **Known builder calls:** CSS/layout, native drag/drop, exact export styling, pagination.
- **Type consistency:** `Source`, `Message`, `Asset`, `Pair`, `Collection`, `CollectionItem`, `State`, `Library`, `ExportResult` are introduced once and referenced by the same names later.
- **Review Focus tests:** stale revision (Task 4/7), path traversal (Task 7), broken active path (Task 1), malicious HTML (Task 5), missing pair (Task 3/6).
