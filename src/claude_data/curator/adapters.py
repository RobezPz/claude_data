import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from claude_data.curator.model import Asset, Message, Source


@dataclass(frozen=True)
class SourceBundle:
    source: Source
    messages: list[Message]
    leaf_uuid: str | None


class SourceAdapter(Protocol):
    provider: str

    def load(self, path: Path, archive_root: Path) -> SourceBundle: ...


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _message_assets(
    raw: dict,
    content: list[dict],
    meta: dict,
    source_id: str,
    message_uuid: str,
    generated_entries: list[dict],
    used_generated: set[str],
) -> list[Asset]:
    assets: dict[str, Asset] = {}

    for item in raw.get("files") or []:
        if not isinstance(item, dict) or not item.get("file_uuid"):
            continue
        file_uuid = str(item["file_uuid"])
        document = item.get("document_asset")
        remote_url = item.get("preview_url") or (
            document.get("url") if isinstance(document, dict) else None
        )
        assets[f"file:{source_id}:{file_uuid}"] = Asset(
            asset_id=f"file:{source_id}:{file_uuid}",
            kind="image" if item.get("file_kind") == "image" else "file",
            label=str(item.get("file_name") or file_uuid),
            local_path=item.get("local_path"),
            remote_url=remote_url,
            message_uuid=message_uuid,
        )

    searchable = json.dumps(raw, ensure_ascii=False)
    for url, local_path in (meta.get("media") or {}).items():
        if url not in searchable and local_path not in searchable:
            continue
        assets[f"media:{source_id}:{_short_hash(url)}"] = Asset(
            asset_id=f"media:{source_id}:{_short_hash(url)}",
            kind="media",
            label=Path(local_path).name,
            local_path=local_path,
            remote_url=url,
            message_uuid=message_uuid,
        )

    artifacts = meta.get("artifacts") or {}
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == "artifacts":
            data = block.get("input") if isinstance(block.get("input"), dict) else {}
            artifact_id = str(data.get("id") or "")
            entry = artifacts.get(artifact_id)
            if artifact_id and isinstance(entry, dict):
                assets[f"artifact:{source_id}:{artifact_id}"] = Asset(
                    asset_id=f"artifact:{source_id}:{artifact_id}",
                    kind="artifact",
                    label=str(data.get("title") or artifact_id),
                    local_path=entry.get("path"),
                    artifact_id=artifact_id,
                    message_uuid=message_uuid,
                )

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result" or block.get("name") != "present_files":
            continue
        for item in block.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "local_resource":
                continue
            file_path = item.get("file_path")
            if not file_path:
                continue
            for entry in generated_entries:
                source_paths = entry.get("source_paths") or []
                if file_path not in source_paths:
                    continue
                used_generated.add(entry["path"])
                asset_id = f"generated:{source_id}:{entry['path']}"
                assets[asset_id] = Asset(
                    asset_id=asset_id,
                    kind="generated",
                    label=Path(file_path).name,
                    local_path=entry["path"],
                    source_paths=tuple(source_paths),
                    message_uuid=message_uuid,
                )
                break

    return list(assets.values())


class ClaudeAdapter:
    provider = "claude"

    def load(self, path: Path, archive_root: Path) -> SourceBundle:
        payload = json.loads(path.read_text())
        meta_path = path.with_name(f"{path.stem}.meta.json")
        meta = (
            json.loads(meta_path.read_text())
            if meta_path.is_file()
            else {}
        )
        if not isinstance(meta, dict):
            meta = {}
        external_id = str(payload.get("uuid") or path.stem)
        source_id = f"{self.provider}:{external_id}"
        try:
            raw_path = str(path.relative_to(archive_root))
        except ValueError:
            raw_path = str(path)
        generated_entries = [
            entry
            for entry in (meta.get("generated") or [])
            if isinstance(entry, dict) and entry.get("path")
        ]
        used_generated: set[str] = set()
        messages: list[Message] = []

        for default_index, raw in enumerate(payload.get("chat_messages") or []):
            if not isinstance(raw, dict):
                continue
            message_uuid = str(raw.get("uuid") or f"{external_id}-{default_index}")
            content = raw.get("content") if isinstance(raw.get("content"), list) else []
            try:
                index = int(raw.get("index", default_index))
            except (TypeError, ValueError):
                index = default_index
            assets = _message_assets(
                raw,
                content,
                meta,
                source_id,
                message_uuid,
                generated_entries,
                used_generated,
            )
            messages.append(
                Message(
                    external_id=message_uuid,
                    parent_id=raw.get("parent_message_uuid") or None,
                    role=str(raw.get("sender") or ""),
                    created_at=str(raw.get("created_at") or ""),
                    index=index,
                    content=content,
                    attachments=raw.get("attachments")
                    if isinstance(raw.get("attachments"), list)
                    else [],
                    assets=assets,
                )
            )

        source_assets = [
            Asset(
                asset_id=f"generated:{source_id}:{entry['path']}",
                kind="generated",
                label=Path(entry["path"]).name,
                local_path=entry["path"],
                source_paths=tuple(entry.get("source_paths") or []),
            )
            for entry in generated_entries
            if entry["path"] not in used_generated
        ]
        source = Source(
            source_id=source_id,
            provider=self.provider,
            external_id=external_id,
            title=str(payload.get("name") or external_id),
            model=str(payload.get("model") or ""),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            raw_path=raw_path,
            message_count=len(payload.get("chat_messages") or []),
            assets=source_assets,
        )
        return SourceBundle(
            source=source,
            messages=messages,
            leaf_uuid=payload.get("current_leaf_message_uuid"),
        )
