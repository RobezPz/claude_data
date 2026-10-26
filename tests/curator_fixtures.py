import json
from pathlib import Path


def message(
    uuid,
    sender,
    index,
    parent="00000000-0000-4000-8000-000000000000",
    text="",
    created="2026-01-01T00:00:00Z",
):
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
