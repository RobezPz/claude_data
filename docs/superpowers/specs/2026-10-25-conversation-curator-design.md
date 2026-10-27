# Conversation Curator — Design

Date: 2026-10-25
Status: Approved 2026-10-25
Scope: A local, purpose-built web editor for curating archived LLM conversations.

## Intent

The archive is good at preserving conversations but not at helping a person
think across them. The user wants to:

- see question/response pairs on a date timeline to follow progression;
- reorder and remove pairs across conversations;
- combine pairs from related conversations into topic collections;
- split one conversation's pairs across several topics;
- add conversations from other LLM providers later, ordered by date.

The curator is a **curation layer over immutable archives**: it composes
collections from references to archived pairs and never rewrites the raw
conversation payloads.

## Goals

- A local-only web app started by `uv run claude-data view`.
- Browse all archived pairs, filter them, and see their dates and provider.
- Create topic collections and add/remove pairs from multiple conversations.
- Reorder pairs manually or sort a collection by date.
- Split a collection at a pair; merge selected pairs into a collection.
- Export a collection to Markdown, HTML, and JSON.
- Preserve raw archive data and allow the archive to be refreshed without
  losing curated work.
- Make a future ChatGPT (or other provider) adapter a data-layer addition, not
  a rewrite.

## Non-goals

- Downloading or importing ChatGPT conversations now.
- Editing the text of archived messages.
- Importing/restoring collection JSON through the UI in v1; backup is copying
  `curated/collections.json`.
- Multi-user accounts, authentication, cloud sync, or remote access.
- LLM calls, embeddings, automatic topic clustering, or summarization.
- A mobile-first UI or a general-purpose note-taking app.

## Anti-goals

- Destructive edits to `archive/`.
- Silent data loss when collections are saved or the archive is refreshed.
- A build step that requires Node/npm for a Python-local tool.
- External CDN/network dependencies.
- Hiding provenance (source, date, conversation) in the curated view.

## User workflow

1. Start the app; the library shows all pairs grouped/filterable by date.
2. Create a collection for a topic.
3. Drag pairs from the library into the collection, or select several and add
   them at once.
4. Reorder manually; use "sort by date" when chronology matters.
5. Split at a pair to start a new collection from the tail; remove pairs that
   do not belong.
6. Preview a pair's full question/answer, including code, images, and collapsed
   tool activity.
7. Export the collection for reading or sharing.

## Data model

The normalized model is provider-agnostic. Claude is the first adapter.

```text
Source
  source_id       "claude:<conversation uuid>"
  provider        "claude" (future: "chatgpt")
  external_id     provider conversation id
  title
  model
  created_at      ISO-8601 UTC
  updated_at      ISO-8601 UTC
  raw_path        path relative to the archive root
  message_count
  assets          source-level Asset references (e.g. unmatched generated zips)
  path_fallback   true when the active path could not be reconstructed

Pair
  pair_id         "<source_id>:<question message uuid>"
  source_id
  question_uuid
  answer_uuids    ordered list
  date            ISO-8601 UTC from the human message
  question_text   plain-text preview (bounded)
  answer_text     plain-text preview (bounded)
  has_branches    true when any message in the pair has more than one child
  missing_answer  true when no assistant message follows
  assets          list of Asset references

Asset
  asset_id        stable local id
  kind            "image" | "file" | "artifact" | "generated" | "media"
  label
  local_path      path relative to the archive root, or null
  remote_url      optional
  message_uuid    owning message, when applicable
  artifact_id     for artifact assets
  source_paths    for generated zips

Collection
  collection_id   uuid
  title
  description
  items           ordered list of CollectionItem
  created_at
  updated_at

CollectionItem
  pair_id
  source_id       snapshot fallback
  question_uuid   snapshot fallback
  date            snapshot fallback
  preview         snapshot fallback
  note            optional short structural note
```

A pair may appear in any number of collections. Collections store references
plus a small snapshot, not full message text.

### Asset normalization

The raw archive stores assets in several places. The adapter resolves them once
into `Asset` records so preview and export never re-interpret provider payloads:

- `chat_messages[].files[]` with `local_path` → `image`/`file` asset on that
  message.
- `attachments[]` → inline `extracted_content`; it stays in the message content,
  not an `Asset`.
- Citation URLs that match the conversation `.meta.json` `media` map → `media`
  asset on the citing message.
- `artifacts` tool calls → `artifact` asset attached to the pair containing the
  tool call, using the conversation `.meta.json` `artifacts` path.
- `present_files` `local_resource` paths → `generated` zip asset on the pair
  whose tool result contains that path, using the conversation `.meta.json`
  `generated` entry whose `source_paths` contains it. A zip that covers several
  pairs is linked from each; an unmatched zip becomes a source-level asset.
- Assets with no local path remain visible as remote/unavailable; they never
  break a pair.

The pair's rendered asset list is the union of its question/answer message
assets. Source-level assets appear in a “Generated files” section.

### Persistence

- Derived pair/source data is built in memory from `archive/conversations/*.json`
  at server start. It is not a source of truth and needs no stored cache for the
  current archive size (~3,400 messages).
- User-authored collections live in `curated/collections.json` (configurable
  `--data-dir`). It is user data, not regenerable, and is gitignored by default.
- The file is `{"version": 1, "revision": N, "collections": [...]}`. The revision
  increments on every successful save.
- Writes are atomic (temp file + `os.replace`), guarded by an in-process lock and
  an `fcntl` file lock, and keep the previous file as `collections.json.bak`.
- **Concurrency:** the client sends the revision it loaded; a stale save gets
  `409 Conflict`, and the UI offers reload or overwrite. One-tab use is the
  normal case, but a stale tab must never silently erase another tab's work.
- **No import/restore UI in v1.** Backup is copying `curated/collections.json`;
  the `.bak` file is an automatic safety net. JSON export is for interchange,
  not restore.

### Stable references across re-archives

Message UUIDs are expected to persist across `--refresh`, but that is not
guaranteed for edited/branched conversations. On load, an unresolved item is
re-linked **in memory** in this order:

1. exact `pair_id`;
2. same `source_id` + `question_uuid`;
3. same `source_id` + same date + matching preview prefix.

Re-links are persisted only on the next user edit; opening the app never
rewrites `collections.json` or its backup. Unresolved items remain in the
collection as `missing`, are visually marked, and can be removed. They are never
silently dropped.

## Pair extraction rules

The archived payload has a flat `chat_messages` list with
`parent_message_uuid`, `current_leaf_message_uuid`, `sender`, `created_at`, and
`content` blocks. Fifteen conversations have branch/fallback structure: 13 have
more than one child on the active path, and 2 lack a
`current_leaf_message_uuid`.

- Build a UUID lookup. Reconstruct the **active path** by walking from
  `current_leaf_message_uuid` through `parent_message_uuid` and reversing.
- If the leaf is missing or broken, fall back to the payload's list/`index`
  order and mark the source as `path_fallback`.
- Walk the active path in order. A `human` message starts a pair; all following
  `assistant` messages belong to that pair until the next `human` message.
- Assistant messages before the first human are ignored (and counted in a
  diagnostic).
- A human message with no following assistant message becomes a pair with
  `missing_answer = true`.
- `date` is the human message `created_at`, normalized to UTC. If missing, use
  the conversation `created_at`, then the Unix epoch.
- `has_branches` is true if any message in the pair has more than one child in
  the full payload.
- Text previews concatenate `content[].text` for `text` blocks, falling back to
  the message `text` field, trimmed and bounded (approximately 200 characters).
- Tool calls/results, thinking, images, attachments, and files remain in the
  pair's full content and are rendered by the preview; they do not affect
  pairing.

## Provider adapters

```python
class SourceAdapter(Protocol):
    provider: str
    def load(self, path: Path) -> SourceBundle: ...
```

`SourceBundle` contains the `Source` plus normalized messages:

```text
Message
  external_id
  parent_id
  role            "human" | "assistant"
  created_at      ISO-8601 UTC
  content         list of provider-neutral blocks
  attachments     inline attachment text
  assets          list of Asset references
```

The pair builder consumes only this interface. Asset resolution is the adapter's
job: a future provider maps its own file/artifact records into `Asset` once, and
preview/export remain provider-independent. v1 ships a `ClaudeAdapter` that
wraps the existing payload. A future `ChatGPTAdapter` maps its export into the
same `SourceBundle`; no collector/downloader is in scope now.

## Server

- `uv run claude-data view [--port 8765] [--archive archive] [--data-dir curated]
  [--no-browser]`.
- Python standard-library threaded HTTP server bound to `127.0.0.1`; opens the
  default browser unless `--no-browser`.
- Static frontend served from `src/claude_data/viewer/` (no Node build).
- JSON API, all local:
  - `GET  /api/library` — sources + pair metadata (previews, dates, assets).
  - `GET  /api/pairs/{pair_id}` — full question/answer content and assets.
  - `GET  /api/state` — `{revision, collections}`.
  - `PUT  /api/state` — `{expected_revision, collections}`; `409` on a stale
    revision.
  - `POST /api/reload` — rebuild the library from disk.
  - `GET  /api/export/{id}.zip` — self-contained export bundle.
  - `GET  /assets/...` — read-only files under the configured archive root.
- The library is built at startup and cached, but every request re-reads a
  cheap archive stamp (`conversations/` directory mtime plus `index.json`
  mtime and size). If `sync` changed the archive, the library rebuilds before
  the response, so a running server picks up new conversations, files, and
  provenance on the next browser refresh without a restart.
- One atomic `PUT /api/state` is deliberate: create, rename, delete, reorder,
  merge, and split all become one validated document write, so concurrent edits
  cannot interleave.
- Path traversal is rejected; only files under the archive root are served.
- Writes are serialized with an in-process lock and an `fcntl` file lock;
  validation errors return JSON with a human-readable message.
- The server never fetches external URLs.

## UI

Desktop-first single-page app, usable on a laptop.

### Layout

- **Top bar**: collection selector, new collection, search, source filter,
  date range, “sort by date” action, save status, export menu.
- **Library pane** (left): all pairs, each with provider badge, date,
  conversation title, question preview, and branch/missing badges.
  Multi-select and drag source.
- **Collection pane** (center): ordered cards with drag handle, date, source,
  question/answer preview, remove button, and optional note.
- **Preview pane** (right or bottom): full question/answer for the selected
  pair; Markdown, code blocks, images; thinking/tool blocks collapsed; pair and
  source asset lists.

### Interactions

- Drag a pair from the library into a collection; drag within a collection to
  reorder. Move-up/move-down buttons provide a keyboard-accessible fallback.
- Select several pairs → "Add to collection" or "New collection from selection".
- "Split here": the selected item and all following items move into a new
  collection; the current collection keeps the head.
- "Merge collections": append one collection's items to another.
- Remove item from collection (collection-scoped, never deletes the pair).
- **Manual order is canonical.** "Sort by date" is a one-shot rewrite of the
  item order; the UI then returns to manual mode. Adding a pair appends to the
  end. A persistent auto-chronological collection is a later option.
- Optional per-item note and collection description (structural metadata only).
- Search filters the library by question/answer preview; filters by provider,
  conversation, and date range.
- A "reload library" action rebuilds pairs after an archive refresh and reports
  unresolved collection items. Since the server re-stamps the archive on every
  request, a plain browser refresh already picks up a sync; `/api/reload`
  remains the explicit trigger.

### Rendering

- Render `text` blocks as Markdown; sanitize all HTML. Vendor the Markdown
  renderer and sanitizer locally; do not use CDNs.
- Render `thinking` and `tool_use`/`tool_result` blocks as collapsed details.
- Render images from archived `files`/`media` via `/assets/`; link files,
  artifacts, and generated zips to their local paths.
- Show dates in the browser's local timezone with a UTC tooltip.
- Never execute scripts from message content.

## Export

Exports are **self-contained**. The primary action downloads a zip containing:

```text
<collection-slug>/
  collection.md
  collection.html
  collection.json
  assets/...
```

- **Markdown**: collection title and description, then each item as
  `## <date> — <source title>`, followed by the question and answer.
  Include source URLs and links to copied assets. Add YAML frontmatter with
  collection id, title, item count, and export date.
- **HTML**: standalone page with embedded CSS, sanitized content, and relative
  links inside the bundle for offline reading.
- **JSON**: the collection plus snapshot metadata, suitable for interchange.
- Referenced images/files/artifacts/generated zips are copied into `assets/`
  with collision-safe names, deduplicated by content hash. Large assets are
  copied as-is; an export can be large, and the UI says so before starting.
- The same folder is written to `curated/exports/<collection-id>/` so the user
  can open it locally without unzipping.
- The zip is assembled in a temporary directory and streamed; it is not kept
  alongside the folder.

## Security and privacy

- Local-only: bind `127.0.0.1`, no authentication, no remote access.
- Serve only files under the configured archive root.
- Sanitize rendered HTML; no raw HTML/scripts from messages.
- No external network calls from the app or frontend.
- Do not log message text, collection content, or credentials.
- `curated/` and `archive/` are sensitive; backups are the user's
  responsibility. The UI should state this once.

## Testing

Unit tests, no browser needed unless noted:

- Pair extraction: simple pair, multiple assistant messages, tool blocks,
  missing answer, branch active path, broken leaf fallback, timestamp fallback.
- Asset normalization: uploaded files, citation media, artifacts, generated
  zips, unmatched zip fallback, missing local path.
- Adapter contract: Claude payload → `SourceBundle`; future adapter can satisfy
  the same protocol.
- Collection operations: create/add/remove/reorder/split/merge, date sort,
  validation, missing-pair re-link.
- Persistence: atomic save, backup, load, schema version, malformed file,
  stale-revision `409`, file-lock behavior.
- Export: self-contained bundle, Markdown/HTML/JSON structure, dates, source
  labels, escaping, asset copying/dedup.
- API: library, pair detail, state get/put, export, path traversal, bad JSON.
- Frontend interactions and drag/drop are manually verified; no JS test harness
  in v1.

## Milestones

1. **Library** — adapter + asset normalization + pair extraction + `view`
   command + read-only timeline/filter UI.
2. **Collections** — create/rename/delete, add/remove, drag reorder, revisioned
   persistence, date-sort action.
3. **Composition** — split at pair, new collection from selection, merge
   collections, optional notes.
4. **Export** — self-contained Markdown/HTML/JSON zip bundle.
5. **Polish** — search, filters, reload/unresolved handling, keyboard access,
   large-export warning.
6. **Later** — ChatGPT adapter, SQLite/FTS, undo/redo, tags, automatic topic
   suggestions, import/restore UI.

## Builder's calls

These are deliberately left to the implementer:

- Exact CSS/layout and visual design.
- Native HTML5 drag/drop versus a small vendored sortable library.
- Markdown renderer/sanitizer and code-highlighting choice (must be vendored).
- Exact Markdown/HTML export styling and asset filename scheme.
- Whether to keep the library JSON as one payload or paginate it; the current
  dataset is small enough for one payload.
