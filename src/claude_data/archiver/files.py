import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from claude_data.archiver.store import (
    atomic_write_bytes,
    atomic_write_json,
    conversation_path,
    meta_path,
)

CLAUDE_ORIGIN = "https://claude.ai"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME_LENGTH = 120


@dataclass(frozen=True)
class FileRef:
    conversation: str
    file_uuid: str
    file_name: str
    kind: str
    size_bytes: int | None
    url: str | None
    variant: str | None
    reason: str | None = None
    content: str | None = None


@dataclass(frozen=True)
class Fetched:
    data: bytes
    content_type: str | None = None


@dataclass(frozen=True)
class PlannedFile:
    ref: FileRef
    remote_url: str
    local_path: Path
    content: str | None = None


@dataclass(frozen=True)
class FilePlan:
    to_download: list[PlannedFile]
    results: list["FileResult"]


@dataclass(frozen=True)
class FileResult:
    ref: FileRef
    status: str
    local_path: str | None = None
    reason: str | None = None
    size: int | None = None


class DownloadError(RuntimeError):
    pass


def error_reason(exc: Exception) -> str:
    if isinstance(exc, (DownloadError, OSError)):
        return str(exc)
    return f"request failed ({type(exc).__name__})"


def resolve_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return f"{CLAUDE_ORIGIN}/{url.lstrip('/')}"


def safe_filename(name: str, fallback: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _CONTROL_CHARS.sub("_", base)
    if base in ("", ".", ".."):
        base = fallback
    if len(base) > _MAX_FILENAME_LENGTH:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 10:
            base = f"{stem[: _MAX_FILENAME_LENGTH - len(ext) - 1]}.{ext}"
        else:
            base = base[:_MAX_FILENAME_LENGTH]
    return base


def local_filename(ref: FileRef) -> str:
    base = safe_filename(ref.file_name, "")
    if ref.variant in ("preview", "thumbnail"):
        stem = base.rsplit(".", 1)[0] if "." in base else base
        suffix = f"-{stem}" if stem else ""
        return f"{ref.file_uuid}{suffix}.webp"
    return f"{ref.file_uuid}-{base or ref.file_uuid}"


def _file_url(item: dict) -> tuple[str | None, str | None]:
    document = item.get("document_asset")
    if isinstance(document, dict) and document.get("url"):
        return document["url"], "original"
    preview = item.get("preview_url")
    if not preview and isinstance(item.get("preview_asset"), dict):
        preview = item["preview_asset"].get("url")
    if preview:
        return preview, "preview"
    thumbnail = item.get("thumbnail_url")
    if not thumbnail and isinstance(item.get("thumbnail_asset"), dict):
        thumbnail = item["thumbnail_asset"].get("url")
    if thumbnail:
        return thumbnail, "thumbnail"
    return None, None


# Tool calls that write a file's full text content, and the input keys holding
# the path and the content. `Write` is the current name; `create_file` is the
# older text-editor tool.
_WRITE_TOOLS = {
    "Write": ("file_path", "content"),
    "create_file": ("path", "file_text"),
}

# Tool calls that mutate an existing file. A delivered file that was edited
# after its last full write cannot be reconstructed from the write content.
_EDIT_TOOLS = ("Edit", "MultiEdit", "str_replace", "str_replace_editor", "NotebookEdit")


def written_file_contents(payload: dict) -> tuple[dict[str, str], set[str]]:
    """Map written file basenames to their final content, and to edited ones."""
    written: dict[str, str] = {}
    edited: set[str] = set()
    for message in payload.get("chat_messages", []):
        if not isinstance(message, dict):
            continue
        for item in message.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            name = item.get("name")
            data = item.get("input")
            if not isinstance(data, dict):
                continue
            keys = _WRITE_TOOLS.get(name)
            if keys is not None:
                path_key, content_key = keys
                path = data.get(path_key)
                content = data.get(content_key)
                if isinstance(path, str) and isinstance(content, str):
                    basename = safe_filename(path, "")
                    written[basename] = content
                    edited.discard(basename)
            elif name in _EDIT_TOOLS:
                path = data.get("file_path") or data.get("path")
                if isinstance(path, str):
                    edited.add(safe_filename(path, ""))
    return written, edited


def extract_file_refs(conversation: str, payload: dict) -> list[FileRef]:
    written, edited = written_file_contents(payload)
    refs: dict[str, FileRef] = {}
    for message in payload.get("chat_messages", []):
        if not isinstance(message, dict):
            continue
        for item in message.get("files") or []:
            if not isinstance(item, dict) or not item.get("file_uuid"):
                continue
            file_uuid = str(item["file_uuid"])
            if file_uuid in refs:
                continue
            url, variant = _file_url(item)
            reason: str | None = None
            content: str | None = None
            if url:
                reason = None
            else:
                basename = safe_filename(str(item.get("file_name") or ""), "")
                if basename in written:
                    if basename in edited:
                        reason = "delivered file was edited after creation"
                    else:
                        content = written[basename]
                else:
                    reason = "no downloadable URL in payload"
            refs[file_uuid] = FileRef(
                conversation=conversation,
                file_uuid=file_uuid,
                file_name=str(item.get("file_name") or ""),
                kind=str(item.get("file_kind") or "file"),
                size_bytes=(
                    item["size_bytes"]
                    if isinstance(item.get("size_bytes"), int)
                    else None
                ),
                url=url,
                variant=variant,
                reason=reason,
                content=content,
            )
    return list(refs.values())


def plan_file_downloads(
    refs: list[FileRef],
    root: Path,
    max_bytes: int | None = None,
    force: bool = False,
) -> FilePlan:
    to_download: list[PlannedFile] = []
    results: list[FileResult] = []
    for ref in refs:
        if not ref.url and ref.content is None:
            results.append(
                FileResult(
                    ref=ref,
                    status="unavailable",
                    reason=ref.reason or "no downloadable URL",
                )
            )
            continue
        local_path = root / "files" / ref.conversation / local_filename(ref)
        local_relative = local_path.relative_to(root).as_posix()
        if local_path.exists() and not force:
            results.append(
                FileResult(ref=ref, status="present", local_path=local_relative)
            )
            continue
        if ref.content is not None:
            size = len(ref.content.encode("utf-8"))
            if max_bytes is not None and max_bytes > 0 and size > max_bytes:
                results.append(
                    FileResult(
                        ref=ref,
                        status="skipped",
                        reason=f"exceeds max size ({size} bytes)",
                    )
                )
                continue
            to_download.append(
                PlannedFile(
                    ref=ref,
                    remote_url="",
                    local_path=local_path,
                    content=ref.content,
                )
            )
            continue
        if (
            max_bytes is not None
            and max_bytes > 0
            and ref.variant == "original"
            and ref.size_bytes is not None
            and ref.size_bytes > max_bytes
        ):
            results.append(
                FileResult(
                    ref=ref,
                    status="skipped",
                    reason=f"exceeds max size ({ref.size_bytes} bytes)",
                )
            )
            continue
        to_download.append(
            PlannedFile(
                ref=ref,
                remote_url=resolve_url(ref.url) if ref.url else "",
                local_path=local_path,
            )
        )
    return FilePlan(to_download=to_download, results=results)


def download_files(
    plan: FilePlan,
    fetch: Callable[[str], Fetched],
    root: Path,
    max_bytes: int | None = None,
) -> list[FileResult]:
    results = list(plan.results)
    for planned in plan.to_download:
        if planned.content is not None:
            data = planned.content.encode("utf-8")
            if max_bytes is not None and max_bytes > 0 and len(data) > max_bytes:
                results.append(
                    FileResult(
                        ref=planned.ref,
                        status="skipped",
                        reason=f"exceeds max size ({len(data)} bytes)",
                    )
                )
                continue
            atomic_write_bytes(planned.local_path, data)
            results.append(
                FileResult(
                    ref=planned.ref,
                    status="exported",
                    local_path=planned.local_path.relative_to(root).as_posix(),
                    size=len(data),
                )
            )
            continue
        try:
            fetched = fetch(planned.remote_url)
        except Exception as exc:  # noqa: BLE001 - failures are recorded, not raised
            results.append(
                FileResult(
                    ref=planned.ref, status="failed", reason=error_reason(exc)
                )
            )
            continue
        if max_bytes is not None and max_bytes > 0 and len(fetched.data) > max_bytes:
            results.append(
                FileResult(
                    ref=planned.ref,
                    status="skipped",
                    reason=f"exceeds max size ({len(fetched.data)} bytes)",
                )
            )
            continue
        atomic_write_bytes(planned.local_path, fetched.data)
        results.append(
            FileResult(
                ref=planned.ref,
                status="downloaded",
                local_path=planned.local_path.relative_to(root).as_posix(),
                size=len(fetched.data),
            )
        )
    return results


def apply_file_results(payload: dict, results: list[FileResult]) -> dict:
    local_paths = {
        result.ref.file_uuid: result.local_path
        for result in results
        if result.local_path
    }
    updated = copy.deepcopy(payload)

    def annotate(node: object) -> None:
        if isinstance(node, dict):
            files = node.get("files")
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict):
                        local_path = local_paths.get(item.get("file_uuid"))
                        if local_path:
                            item["local_path"] = local_path
            for value in node.values():
                annotate(value)
        elif isinstance(node, list):
            for item in node:
                annotate(item)

    annotate(updated)
    return updated


def process_archived_files(
    root: Path,
    conversations: list[tuple[str, dict]],
    fetch: Callable[[str], Fetched],
    max_bytes: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[FileResult]:
    all_results: list[FileResult] = []
    for conversation, payload in conversations:
        refs = extract_file_refs(conversation, payload)
        plan = plan_file_downloads(refs, root, max_bytes=max_bytes, force=force)
        if dry_run:
            results = list(plan.results)
            for planned in plan.to_download:
                results.append(
                    FileResult(
                        ref=planned.ref,
                        status="planned",
                        local_path=planned.local_path.relative_to(root).as_posix(),
                    )
                )
        else:
            results = download_files(plan, fetch, root, max_bytes=max_bytes)
            if results:
                atomic_write_json(
                    conversation_path(root, conversation),
                    apply_file_results(payload, results),
                )
                meta_file = meta_path(root, conversation)
                meta = (
                    json.loads(meta_file.read_text()) if meta_file.is_file() else {}
                )
                atomic_write_json(meta_file, update_meta(meta, results))
        all_results.extend(results)
    return all_results


def update_meta(meta: dict, results: list[FileResult]) -> dict:
    files = dict(meta.get("files", {}))
    current = {result.ref.file_uuid for result in results}
    failures = [
        failure
        for failure in meta.get("file_failures", [])
        if failure.get("file_uuid") not in current
    ]
    for result in results:
        entry: dict = {
            "file_name": result.ref.file_name,
            "kind": result.ref.kind,
            "variant": result.ref.variant,
            "size_bytes": result.ref.size_bytes,
            "status": result.status,
        }
        if result.local_path:
            entry["path"] = result.local_path
        if result.size is not None:
            entry["downloaded_bytes"] = result.size
        if result.reason:
            entry["reason"] = result.reason
        files[result.ref.file_uuid] = entry
        if result.status in ("failed", "unavailable"):
            failures.append(
                {
                    "file_uuid": result.ref.file_uuid,
                    "reason": result.reason or "",
                }
            )
    return {**meta, "files": files, "file_failures": failures}
