import json
from dataclasses import dataclass
from pathlib import Path

from claude_data.archiver.files import safe_filename
from claude_data.archiver.store import (
    atomic_write_bytes,
    atomic_write_json,
    meta_path,
)


@dataclass(frozen=True)
class Artifact:
    conversation: str
    artifact_id: str
    title: str
    type: str
    language: str
    version_uuid: str
    content: str


@dataclass(frozen=True)
class ArtifactResult:
    artifact: Artifact
    status: str
    local_path: str | None = None
    reason: str | None = None


_LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "bash": ".sh",
    "sh": ".sh",
    "shell": ".sh",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "jsx": ".jsx",
    "tsx": ".tsx",
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "csharp": ".cs",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
    "ruby": ".rb",
    "php": ".php",
    "swift": ".swift",
    "kotlin": ".kt",
    "lua": ".lua",
    "r": ".r",
    "perl": ".pl",
    "scala": ".scala",
    "html": ".html",
    "css": ".css",
    "json": ".json",
    "sql": ".sql",
    "yaml": ".yaml",
    "yml": ".yaml",
    "ini": ".ini",
    "toml": ".toml",
    "markdown": ".md",
    "md": ".md",
}

_TYPE_EXTENSIONS = {
    "text/markdown": ".md",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/json": ".json",
    "image/svg+xml": ".svg",
}


def extract_artifacts(conversation: str, payload: dict) -> list[Artifact]:
    artifacts: dict[str, Artifact] = {}
    for message in payload.get("chat_messages", []):
        if not isinstance(message, dict):
            continue
        for item in message.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use" or item.get("name") != "artifacts":
                continue
            data = item.get("input")
            if not isinstance(data, dict):
                continue
            artifact_id = str(data.get("id") or "")
            if not artifact_id:
                continue
            if data.get("command") == "delete":
                artifacts.pop(artifact_id, None)
                continue
            if "content" not in data:
                continue
            previous = artifacts.get(artifact_id)
            artifacts[artifact_id] = Artifact(
                conversation=conversation,
                artifact_id=artifact_id,
                title=str(data.get("title") or (previous.title if previous else "")),
                type=str(data.get("type") or (previous.type if previous else "")),
                language=str(
                    data.get("language") or (previous.language if previous else "")
                ),
                version_uuid=str(
                    data.get("version_uuid")
                    or (previous.version_uuid if previous else "")
                ),
                content=str(data["content"]),
            )
    return list(artifacts.values())


def artifact_extension(artifact: Artifact) -> str:
    language = (artifact.language or "").lower()
    artifact_type = (artifact.type or "").lower()
    if artifact_type == "application/vnd.ant.react":
        return ".tsx" if language in ("typescript", "ts") else ".jsx"
    if language in _LANGUAGE_EXTENSIONS:
        return _LANGUAGE_EXTENSIONS[language]
    if artifact_type in _TYPE_EXTENSIONS:
        return _TYPE_EXTENSIONS[artifact_type]
    return ".txt"


def artifact_filename(artifact: Artifact) -> str:
    stem = safe_filename(artifact.artifact_id, "artifact")
    return f"{stem}{artifact_extension(artifact)}"


def export_artifacts(
    root: Path, artifacts: list[Artifact], force: bool = False
) -> list[ArtifactResult]:
    results: list[ArtifactResult] = []
    for artifact in artifacts:
        target = (
            root
            / "artifacts"
            / artifact.conversation
            / artifact_filename(artifact)
        )
        local_path = target.relative_to(root).as_posix()
        if target.exists() and not force:
            results.append(
                ArtifactResult(
                    artifact=artifact, status="present", local_path=local_path
                )
            )
            continue
        try:
            atomic_write_bytes(target, artifact.content.encode("utf-8"))
            atomic_write_json(
                target.parent / f"{target.stem}.meta.json",
                {
                    "conversation": artifact.conversation,
                    "artifact_id": artifact.artifact_id,
                    "title": artifact.title,
                    "type": artifact.type,
                    "language": artifact.language,
                    "version_uuid": artifact.version_uuid,
                },
            )
        except OSError as exc:
            results.append(
                ArtifactResult(
                    artifact=artifact, status="failed", reason=str(exc)
                )
            )
            continue
        results.append(
            ArtifactResult(
                artifact=artifact, status="downloaded", local_path=local_path
            )
        )
    return results


def update_meta_artifacts(meta: dict, results: list[ArtifactResult]) -> dict:
    artifacts = dict(meta.get("artifacts", {}))
    for result in results:
        entry: dict = {
            "path": result.local_path,
            "status": result.status,
            "title": result.artifact.title,
            "type": result.artifact.type,
            "language": result.artifact.language,
            "version_uuid": result.artifact.version_uuid,
        }
        if result.reason:
            entry["reason"] = result.reason
        artifacts[result.artifact.artifact_id] = entry
    return {**meta, "artifacts": artifacts}


def process_archived_artifacts(
    root: Path,
    conversations: list[tuple[str, dict]],
    force: bool = False,
    dry_run: bool = False,
) -> list[ArtifactResult]:
    all_results: list[ArtifactResult] = []
    for conversation, payload in conversations:
        artifacts = extract_artifacts(conversation, payload)
        if dry_run:
            results = [
                ArtifactResult(
                    artifact=artifact,
                    status="planned",
                    local_path=(
                        root
                        / "artifacts"
                        / conversation
                        / artifact_filename(artifact)
                    )
                    .relative_to(root)
                    .as_posix(),
                )
                for artifact in artifacts
            ]
        else:
            results = export_artifacts(root, artifacts, force=force)
            if results:
                meta_file = meta_path(root, conversation)
                meta = (
                    json.loads(meta_file.read_text()) if meta_file.is_file() else {}
                )
                atomic_write_json(meta_file, update_meta_artifacts(meta, results))
        all_results.extend(results)
    return all_results
