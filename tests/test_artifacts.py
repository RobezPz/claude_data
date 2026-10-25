import json

from claude_data.artifacts import (
    Artifact,
    ArtifactResult,
    artifact_extension,
    artifact_filename,
    export_artifacts,
    extract_artifacts,
    process_archived_artifacts,
    update_meta_artifacts,
)
from claude_data.store import atomic_write_json, conversation_path, meta_path


def make_artifact(**overrides):
    defaults = {
        "conversation": "conv-1",
        "artifact_id": "demo",
        "title": "Demo",
        "type": "application/vnd.ant.code",
        "language": "python",
        "version_uuid": "v1",
        "content": "print('hello')\n",
    }
    defaults.update(overrides)
    return Artifact(**defaults)


def payload_with(*content_items):
    return {"chat_messages": [{"content": list(content_items)}]}


def tool_use(info):
    return {"type": "tool_use", "name": "artifacts", "input": info}


def test_extract_artifacts_keeps_latest_version_per_id():
    payload = payload_with(
        tool_use(
            {
                "id": "demo",
                "command": "create",
                "type": "application/vnd.ant.code",
                "language": "python",
                "title": "Demo",
                "content": "one",
                "version_uuid": "v1",
            }
        ),
        tool_use(
            {
                "id": "demo",
                "command": "rewrite",
                "content": "two",
                "version_uuid": "v2",
            }
        ),
    )

    artifacts = extract_artifacts("conv-1", payload)

    assert len(artifacts) == 1
    assert artifacts[0].content == "two"
    assert artifacts[0].version_uuid == "v2"


def test_extract_artifacts_carries_forward_type_language_and_title():
    payload = payload_with(
        tool_use(
            {
                "id": "demo",
                "command": "create",
                "type": "application/vnd.ant.code",
                "language": "python",
                "title": "Demo",
                "content": "one",
                "version_uuid": "v1",
            }
        ),
        tool_use(
            {
                "id": "demo",
                "command": "update",
                "content": "two",
                "version_uuid": "v2",
            }
        ),
    )

    artifact = extract_artifacts("conv-1", payload)[0]

    assert artifact.type == "application/vnd.ant.code"
    assert artifact.language == "python"
    assert artifact.title == "Demo"


def test_artifact_extension_uses_language_and_type():
    assert artifact_extension(make_artifact(language="python")) == ".py"
    assert artifact_extension(make_artifact(language="bash")) == ".sh"
    assert artifact_extension(make_artifact(language="cpp")) == ".cpp"
    assert (
        artifact_extension(
            make_artifact(type="text/markdown", language=None)
        )
        == ".md"
    )
    assert (
        artifact_extension(
            make_artifact(type="application/vnd.ant.react", language="javascript")
        )
        == ".jsx"
    )
    assert artifact_extension(make_artifact(language=None, type="text/plain")) == ".txt"


def test_artifact_filename_sanitizes_id_and_keeps_extension():
    artifact = make_artifact(artifact_id="my/../evil", language="python")

    name = artifact_filename(artifact)

    assert "/" not in name
    assert name.endswith(".py")


def test_export_artifacts_writes_content_and_metadata_sidecar(tmp_path):
    artifact = make_artifact()

    results = export_artifacts(tmp_path, [artifact])

    assert results[0].status == "downloaded"
    target = tmp_path / results[0].local_path
    assert target.read_text() == "print('hello')\n"
    meta = json.loads((target.parent / "demo.meta.json").read_text())
    assert meta == {
        "conversation": "conv-1",
        "artifact_id": "demo",
        "title": "Demo",
        "type": "application/vnd.ant.code",
        "language": "python",
        "version_uuid": "v1",
    }


def test_export_artifacts_skips_existing_unless_forced(tmp_path):
    artifact = make_artifact()
    target = tmp_path / "artifacts" / "conv-1" / "demo.py"
    target.parent.mkdir(parents=True)
    target.write_text("old")

    skipped = export_artifacts(tmp_path, [artifact])

    assert skipped[0].status == "present"
    assert target.read_text() == "old"

    forced = export_artifacts(tmp_path, [artifact], force=True)

    assert forced[0].status == "downloaded"
    assert target.read_text() == "print('hello')\n"


def test_update_meta_artifacts_preserves_existing_fields(tmp_path):
    meta = {"media": {"u": "media/x"}}
    artifact = make_artifact()
    results = export_artifacts(tmp_path, [artifact])

    updated = update_meta_artifacts(meta, results)

    assert updated["media"] == {"u": "media/x"}
    entry = updated["artifacts"]["demo"]
    assert entry["path"].endswith("demo.py")
    assert entry["status"] == "downloaded"
    assert entry["title"] == "Demo"


def test_update_meta_artifacts_records_failures():
    meta = {}
    artifact = make_artifact()
    results = [
        ArtifactResult(
            artifact=artifact, status="failed", reason="disk full"
        )
    ]

    updated = update_meta_artifacts(meta, results)

    assert updated["artifacts"]["demo"]["status"] == "failed"
    assert updated["artifacts"]["demo"]["reason"] == "disk full"


def test_process_archived_artifacts_exports_and_updates_meta(tmp_path):
    payload = payload_with(
        tool_use(
            {
                "id": "demo",
                "command": "create",
                "type": "application/vnd.ant.code",
                "language": "python",
                "title": "Demo",
                "content": "print('hello')\n",
                "version_uuid": "v1",
            }
        )
    )
    atomic_write_json(conversation_path(tmp_path, "conv-1"), payload)
    atomic_write_json(meta_path(tmp_path, "conv-1"), {"media": {}})

    results = process_archived_artifacts(tmp_path, [("conv-1", payload)])

    assert results[0].status == "downloaded"
    assert (tmp_path / results[0].local_path).read_text() == "print('hello')\n"
    meta = json.loads(meta_path(tmp_path, "conv-1").read_text())
    assert meta["artifacts"]["demo"]["status"] == "downloaded"


def test_process_archived_artifacts_dry_run_writes_nothing(tmp_path):
    payload = payload_with(
        tool_use({"id": "demo", "command": "create", "content": "body"})
    )
    atomic_write_json(conversation_path(tmp_path, "conv-1"), payload)
    atomic_write_json(meta_path(tmp_path, "conv-1"), {"media": {}})

    results = process_archived_artifacts(
        tmp_path, [("conv-1", payload)], dry_run=True
    )

    assert results[0].status == "planned"
    assert not (tmp_path / "artifacts").exists()
