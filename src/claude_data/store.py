import json
import os
from pathlib import Path


def conversation_path(root: Path, uuid: str) -> Path:
    return root / "conversations" / f"{uuid}.json"


def meta_path(root: Path, uuid: str) -> Path:
    return root / "conversations" / f"{uuid}.meta.json"


def media_path(root: Path, filename: str) -> Path:
    return root / "media" / filename


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: object) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"))


def list_archived(root: Path) -> set[str]:
    conversations = root / "conversations"
    if not conversations.is_dir():
        return set()
    return {p.stem for p in conversations.glob("*.json") if not p.name.endswith(".meta.json")}
