import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from claude_data.archiver.files import Fetched, error_reason
from claude_data.archiver.store import atomic_write_bytes, atomic_write_json, meta_path

MAX_BATCH_SIZE = 8


@dataclass(frozen=True)
class GeneratedResult:
    conversation: str
    paths: list[str]
    status: str
    local_path: str | None = None
    reason: str | None = None
    size: int | None = None
    entries: int | None = None
    missing: list[str] | None = None


def extract_generated_paths(payload: dict) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for message in payload.get("chat_messages", []):
        if not isinstance(message, dict):
            continue
        for item in message.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_result" or item.get("name") != "present_files":
                continue
            for resource in item.get("content") or []:
                if not isinstance(resource, dict):
                    continue
                if resource.get("type") != "local_resource":
                    continue
                path = resource.get("file_path")
                if path and path not in seen:
                    seen.add(path)
                    paths.append(path)
    return paths


def chunk_paths(paths: list[str], size: int = MAX_BATCH_SIZE) -> list[list[str]]:
    return [paths[index : index + size] for index in range(0, len(paths), size)]


def generated_url(org: str, conversation: str, paths: list[str]) -> str:
    query = urlencode([("paths", path) for path in paths])
    return (
        "https://claude.ai/api/organizations/"
        f"{org}/conversations/{conversation}/wiggle/download-files?{query}"
    )


def _valid_zip(fetched: Fetched) -> bool:
    return fetched.content_type == "application/zip" and zipfile.is_zipfile(
        io.BytesIO(fetched.data)
    )


def _merge_zips(payloads: list[bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for data in payloads:
            with zipfile.ZipFile(io.BytesIO(data)) as source:
                for info in source.infolist():
                    output.writestr(info, source.read(info))
    return buffer.getvalue()


def _fallback_batch(
    conversation: str,
    batch: list[str],
    fetch_zip: Callable[[str, list[str]], Fetched],
    target: Path,
    local_path: str,
    max_bytes: int | None,
) -> GeneratedResult:
    payloads: list[bytes] = []
    missing: list[str] = []
    for path in batch:
        try:
            fetched = fetch_zip(conversation, [path])
        except Exception:  # noqa: BLE001 - missing files are recorded
            missing.append(path)
            continue
        if not _valid_zip(fetched):
            missing.append(path)
            continue
        if max_bytes is not None and max_bytes > 0 and len(fetched.data) > max_bytes:
            missing.append(path)
            continue
        payloads.append(fetched.data)
    if not payloads:
        return GeneratedResult(
            conversation=conversation,
            paths=batch,
            status="failed",
            reason="no files could be downloaded",
            missing=missing,
        )
    merged = _merge_zips(payloads)
    if max_bytes is not None and max_bytes > 0 and len(merged) > max_bytes:
        return GeneratedResult(
            conversation=conversation,
            paths=batch,
            status="skipped",
            reason=f"exceeds max size ({len(merged)} bytes)",
            missing=missing,
        )
    entries = len(zipfile.ZipFile(io.BytesIO(merged)).namelist())
    atomic_write_bytes(target, merged)
    return GeneratedResult(
        conversation=conversation,
        paths=batch,
        status="downloaded",
        local_path=local_path,
        reason=f"{len(missing)} file(s) unavailable" if missing else None,
        size=len(merged),
        entries=entries,
        missing=missing,
    )


def download_generated_zips(
    root: Path,
    conversation: str,
    batches: list[list[str]],
    fetch_zip: Callable[[str, list[str]], Fetched],
    max_bytes: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[GeneratedResult]:
    results: list[GeneratedResult] = []
    for index, batch in enumerate(batches, start=1):
        target = root / "generated" / conversation / f"files-{index}.zip"
        local_path = target.relative_to(root).as_posix()
        if target.exists() and not force:
            try:
                entries = len(zipfile.ZipFile(target).namelist())
            except (OSError, zipfile.BadZipFile):
                entries = None
            results.append(
                GeneratedResult(
                    conversation=conversation,
                    paths=batch,
                    status="present",
                    local_path=local_path,
                    entries=entries,
                )
            )
            continue
        if dry_run:
            results.append(
                GeneratedResult(
                    conversation=conversation,
                    paths=batch,
                    status="planned",
                    local_path=local_path,
                )
            )
            continue
        try:
            fetched = fetch_zip(conversation, batch)
        except Exception as exc:  # noqa: BLE001 - failures are recorded, not raised
            if len(batch) > 1:
                results.append(
                    _fallback_batch(
                        conversation,
                        batch,
                        fetch_zip,
                        target,
                        local_path,
                        max_bytes,
                    )
                )
            else:
                results.append(
                    GeneratedResult(
                        conversation=conversation,
                        paths=batch,
                        status="failed",
                        reason=error_reason(exc),
                    )
                )
            continue
        if max_bytes is not None and max_bytes > 0 and len(fetched.data) > max_bytes:
            if len(batch) > 1:
                results.append(
                    _fallback_batch(
                        conversation,
                        batch,
                        fetch_zip,
                        target,
                        local_path,
                        max_bytes,
                    )
                )
            else:
                results.append(
                    GeneratedResult(
                        conversation=conversation,
                        paths=batch,
                        status="skipped",
                        reason=f"exceeds max size ({len(fetched.data)} bytes)",
                    )
                )
            continue
        if not _valid_zip(fetched):
            if len(batch) > 1:
                results.append(
                    _fallback_batch(
                        conversation,
                        batch,
                        fetch_zip,
                        target,
                        local_path,
                        max_bytes,
                    )
                )
            else:
                results.append(
                    GeneratedResult(
                        conversation=conversation,
                        paths=batch,
                        status="failed",
                        reason=f"not a zip ({fetched.content_type})",
                    )
                )
            continue
        entries = len(zipfile.ZipFile(io.BytesIO(fetched.data)).namelist())
        atomic_write_bytes(target, fetched.data)
        results.append(
            GeneratedResult(
                conversation=conversation,
                paths=batch,
                status="downloaded",
                local_path=local_path,
                size=len(fetched.data),
                entries=entries,
            )
        )
    return results


def process_archived_generated(
    root: Path,
    conversations: list[tuple[str, dict]],
    fetch_zip: Callable[[str, list[str]], Fetched],
    max_bytes: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[GeneratedResult]:
    all_results: list[GeneratedResult] = []
    for conversation, payload in conversations:
        paths = extract_generated_paths(payload)
        if not paths:
            continue
        results = download_generated_zips(
            root,
            conversation,
            chunk_paths(paths),
            fetch_zip,
            max_bytes=max_bytes,
            force=force,
            dry_run=dry_run,
        )
        if results and not dry_run:
            meta_file = meta_path(root, conversation)
            meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
            atomic_write_json(meta_file, update_meta_generated(meta, results))
        all_results.extend(results)
    return all_results


def update_meta_generated(meta: dict, results: list[GeneratedResult]) -> dict:
    generated = {
        entry["path"]: entry
        for entry in meta.get("generated", [])
        if isinstance(entry, dict) and entry.get("path")
    }
    conversation = results[0].conversation if results else None
    failures = [
        failure
        for failure in meta.get("generated_failures", [])
        if failure.get("conversation") != conversation
    ]
    for result in results:
        if result.local_path:
            existing = generated.get(result.local_path)
            missing = result.missing or (
                existing.get("missing_paths") if existing else None
            )
            entry: dict = {
                "path": result.local_path,
                "status": result.status,
                "file_count": result.entries,
                "source_paths": result.paths,
            }
            if result.size is not None:
                entry["size_bytes"] = result.size
            if result.reason:
                entry["reason"] = result.reason
            if missing:
                entry["missing_paths"] = missing
            generated[result.local_path] = entry
        else:
            missing = result.missing
        if result.status in ("failed", "unavailable"):
            failures.append(
                {
                    "conversation": result.conversation,
                    "paths": result.paths,
                    "reason": result.reason or "",
                }
            )
        elif missing:
            failures.append(
                {
                    "conversation": result.conversation,
                    "paths": missing,
                    "reason": "file not available",
                }
            )
    return {
        **meta,
        "generated": sorted(generated.values(), key=lambda entry: entry["path"]),
        "generated_failures": failures,
    }
