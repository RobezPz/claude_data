import fcntl
import json
import re
import shutil
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from claude_data.archiver.store import atomic_write_json
from claude_data.curator.model import Collection, CollectionItem, State

STATE_VERSION = 1
STATE_FILE = "collections.json"


class StoreError(ValueError):
    pass


class RevisionConflict(RuntimeError):
    pass


@contextmanager
def _locked(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _parse_collection(raw: object) -> Collection:
    if not isinstance(raw, dict):
        raise StoreError("collection must be an object")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list):
        raise StoreError("collection items must be a list")
    items: list[CollectionItem] = []
    for item in items_raw:
        if not isinstance(item, dict):
            raise StoreError("collection item must be an object")
        items.append(
            CollectionItem(
                pair_id=str(item.get("pair_id") or ""),
                source_id=str(item.get("source_id") or ""),
                question_uuid=str(item.get("question_uuid") or ""),
                date=str(item.get("date") or ""),
                preview=str(item.get("preview") or ""),
                note=str(item.get("note") or ""),
            )
        )
    return Collection(
        collection_id=str(raw.get("collection_id") or ""),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        items=items,
        created_at=str(raw.get("created_at") or ""),
        updated_at=str(raw.get("updated_at") or ""),
    )


def validate_collections(collections: list[Collection]) -> None:
    ids: set[str] = set()
    for collection in collections:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", collection.collection_id):
            raise ValueError(f"invalid collection id: {collection.collection_id!r}")
        if collection.collection_id in ids:
            raise ValueError(f"duplicate collection id: {collection.collection_id}")
        ids.add(collection.collection_id)
        if not collection.title.strip():
            raise ValueError(f"collection {collection.collection_id} needs a title")
        pair_ids: set[str] = set()
        for item in collection.items:
            if not item.pair_id or not item.source_id or not item.question_uuid:
                raise ValueError(
                    f"collection {collection.collection_id} has an incomplete item"
                )
            if item.pair_id in pair_ids:
                raise ValueError(
                    f"collection {collection.collection_id} repeats pair {item.pair_id}"
                )
            pair_ids.add(item.pair_id)


def load_state(data_dir: Path) -> State:
    path = data_dir / STATE_FILE
    if not path.is_file():
        return State(revision=0, collections=[])
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StoreError("state must be an object")
    if raw.get("version") != STATE_VERSION:
        raise StoreError(f"unsupported state version: {raw.get('version')!r}")
    revision = raw.get("revision")
    if not isinstance(revision, int) or revision < 0:
        raise StoreError("state revision must be a non-negative integer")
    collections_raw = raw.get("collections")
    if not isinstance(collections_raw, list):
        raise StoreError("state collections must be a list")
    try:
        collections = [_parse_collection(item) for item in collections_raw]
        validate_collections(collections)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    return State(revision=revision, collections=collections)


def save_state(
    data_dir: Path, expected_revision: int, collections: list[Collection]
) -> State:
    data_dir.mkdir(parents=True, exist_ok=True)
    with _locked(data_dir):
        current = load_state(data_dir)
        if current.revision != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, found {current.revision}"
            )
        validate_collections(collections)
        path = data_dir / STATE_FILE
        if path.is_file():
            shutil.copy2(path, data_dir / f"{STATE_FILE}.bak")
        updated = State(revision=current.revision + 1, collections=collections)
        atomic_write_json(
            path,
            {
                "version": STATE_VERSION,
                "revision": updated.revision,
                "collections": [asdict(collection) for collection in updated.collections],
            },
        )
        return updated
