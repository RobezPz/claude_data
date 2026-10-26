import json
import os
from datetime import UTC, datetime
from pathlib import Path

CONVERSATION_SOURCE_URL = "https://claude.ai/chat/{uuid}"


def conversation_path(root: Path, uuid: str) -> Path:
    return root / "conversations" / f"{uuid}.json"


def meta_path(root: Path, uuid: str) -> Path:
    return root / "conversations" / f"{uuid}.meta.json"


def media_path(root: Path, filename: str) -> Path:
    return root / "media" / filename


def file_path(root: Path, conversation: str, filename: str) -> Path:
    return root / "files" / conversation / filename


def artifact_path(root: Path, conversation: str, filename: str) -> Path:
    return root / "artifacts" / conversation / filename


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: object) -> None:
    atomic_write_bytes(
        path, json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")
    )


def list_archived(root: Path) -> set[str]:
    conversations = root / "conversations"
    if not conversations.is_dir():
        return set()
    return {
        p.stem
        for p in conversations.glob("*.json")
        if not p.name.endswith(".meta.json")
    }


def load_archived(root: Path, only: str | None = None) -> list[tuple[str, dict]]:
    conversations = root / "conversations"
    if not conversations.is_dir():
        return []
    loaded: list[tuple[str, dict]] = []
    for path in sorted(conversations.glob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        if only is not None and path.stem != only:
            continue
        loaded.append((path.stem, json.loads(path.read_text())))
    return loaded


def extract_metadata(payload: dict) -> dict:
    messages = payload.get("chat_messages")
    message_count = len(messages) if isinstance(messages, list) else 0
    return {
        "title": str(payload.get("name", "")),
        "created_at": str(payload.get("created_at", "")),
        "updated_at": str(payload.get("updated_at", "")),
        "message_count": message_count,
    }


def build_manifest(root: Path) -> dict:
    entries = []
    for path in sorted((root / "conversations").glob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        uuid = path.stem
        payload = json.loads(path.read_text())
        sidecar: dict = {}
        sidecar_file = meta_path(root, uuid)
        if sidecar_file.is_file():
            sidecar = json.loads(sidecar_file.read_text())
        entries.append(
            {
                "uuid": uuid,
                **extract_metadata(payload),
                "source_url": CONVERSATION_SOURCE_URL.format(uuid=uuid),
                "media": sidecar.get("media", {}),
                "media_failures": sidecar.get("media_failures", []),
                "files": sidecar.get("files", {}),
                "file_failures": sidecar.get("file_failures", []),
                "artifacts": sidecar.get("artifacts", {}),
                "generated": sidecar.get("generated", []),
                "generated_failures": sidecar.get("generated_failures", []),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "conversations": sorted(entries, key=lambda entry: entry["uuid"]),
    }
