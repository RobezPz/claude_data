import json
import mimetypes
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from claude_data.curator.export import export_collection
from claude_data.curator.library import Library
from claude_data.curator.model import Collection, CollectionItem
from claude_data.curator.render import render_pair
from claude_data.curator.store import (
    RevisionConflict,
    StoreError,
    load_state,
    save_state,
)

API_PREFIX = "/api/"
ASSET_PREFIX = "/assets/"


class CuratorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    library: Library
    archive_root: Path
    data_dir: Path
    static_dir: Path

    def __init__(
        self,
        archive_root: Path,
        data_dir: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        super().__init__((host, port), CuratorHandler)
        self.archive_root = Path(archive_root)
        self.data_dir = Path(data_dir)
        self.static_dir = Path(__file__).parent / "static"
        self.library = Library.build(self.archive_root)


def _collection_from_dict(raw: object) -> Collection:
    if not isinstance(raw, dict):
        raise TypeError("collection must be an object")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list):
        raise TypeError("collection items must be a list")
    items = [
        CollectionItem(
            pair_id=str(item.get("pair_id") or ""),
            source_id=str(item.get("source_id") or ""),
            question_uuid=str(item.get("question_uuid") or ""),
            date=str(item.get("date") or ""),
            preview=str(item.get("preview") or ""),
            note=str(item.get("note") or ""),
        )
        for item in items_raw
        if isinstance(item, dict)
    ]
    if len(items) != len(items_raw):
        raise ValueError("collection item must be an object")
    return Collection(
        collection_id=str(raw.get("collection_id") or ""),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        items=items,
        created_at=str(raw.get("created_at") or ""),
        updated_at=str(raw.get("updated_at") or ""),
    )


class CuratorHandler(BaseHTTPRequestHandler):
    server: CuratorServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(
        self, data: bytes, content_type: str, status: int = 200, filename: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: object, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
            status,
        )

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            raise ValueError("request body is required")
        return json.loads(raw)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        if path == "/api/library":
            self._send_json(self.server.library.to_json())
            return

        if path.startswith(API_PREFIX + "pairs/"):
            pair_id = path[len(API_PREFIX + "pairs/") :]
            try:
                content = self.server.library.pair_content(pair_id)
            except KeyError:
                self._error(404, f"unknown pair: {pair_id}")
                return
            pair = self.server.library.pair_by_id[pair_id]
            messages = self.server.library.pair_messages[pair_id]
            rendered = render_pair(pair, messages[0], messages[1:])
            self._send_json(
                {
                    "pair": content["pair"],
                    "question_html": rendered["question_html"],
                    "answer_html": rendered["answer_html"],
                    "assets": rendered["assets"],
                }
            )
            return

        if path == "/api/state":
            try:
                state = load_state(self.server.data_dir)
            except (StoreError, OSError) as exc:
                self._error(
                    500,
                    f"cannot read collections.json: {exc}; "
                    "restore from collections.json.bak",
                )
                return
            collections = self.server.library.reconcile(state.collections)
            self._send_json(
                {
                    "revision": state.revision,
                    "collections": [asdict(collection) for collection in collections],
                }
            )
            return

        if path.startswith(API_PREFIX + "export/") and path.endswith(".zip"):
            collection_id = path[len(API_PREFIX + "export/") : -4]
            try:
                state = load_state(self.server.data_dir)
            except (StoreError, OSError) as exc:
                self._error(
                    500,
                    f"cannot read collections.json: {exc}; "
                    "restore from collections.json.bak",
                )
                return
            collections = self.server.library.reconcile(state.collections)
            collection = next(
                (
                    candidate
                    for candidate in collections
                    if candidate.collection_id == collection_id
                ),
                None,
            )
            if collection is None:
                self._error(404, f"unknown collection: {collection_id}")
                return
            result = export_collection(
                collection,
                self.server.library,
                self.server.archive_root,
                self.server.data_dir / "exports",
            )
            self._send_bytes(
                result.zip_path.read_bytes(),
                "application/zip",
                filename=f"{collection_id}.zip",
            )
            return

        if path.startswith(ASSET_PREFIX):
            self._send_asset(path[len(ASSET_PREFIX) :])
            return

        self._send_static(path)

    def do_PUT(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path != "/api/state":
            self._error(404, f"unknown path: {path}")
            return
        try:
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise TypeError("state body must be an object")
            expected = payload.get("expected_revision")
            if not isinstance(expected, int):
                raise TypeError("expected_revision must be an integer")
            raw_collections = payload.get("collections")
            if not isinstance(raw_collections, list):
                raise TypeError("collections must be a list")
            collections = [_collection_from_dict(item) for item in raw_collections]
            updated = save_state(self.server.data_dir, expected, collections)
        except RevisionConflict as exc:
            self._error(409, str(exc))
            return
        except (ValueError, TypeError, json.JSONDecodeError, StoreError) as exc:
            self._error(400, str(exc))
            return
        self._send_json(
            {
                "revision": updated.revision,
                "collections": [asdict(collection) for collection in updated.collections],
            }
        )

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path != "/api/reload":
            self._error(404, f"unknown path: {path}")
            return
        self.server.library = Library.build(self.server.archive_root)
        self._send_json(
            {
                "sources": len(self.server.library.sources),
                "pairs": len(self.server.library.pairs),
            }
        )

    def _send_asset(self, relative: str) -> None:
        root = self.server.archive_root.resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._error(404, "asset not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_bytes(target.read_bytes(), content_type)

    def _send_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (self.server.static_dir / relative).resolve()
        if not target.is_relative_to(self.server.static_dir.resolve()) or not target.is_file():
            self._error(404, "not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_bytes(target.read_bytes(), content_type)


def create_server(
    archive_root: Path,
    data_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> CuratorServer:
    return CuratorServer(archive_root, data_dir, host=host, port=port)


def serve(
    archive_root: Path,
    data_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = create_server(archive_root, data_dir, host=host, port=port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Conversation curator: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
