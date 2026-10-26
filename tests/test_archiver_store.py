import json

from claude_data.archiver.store import (
    artifact_path,
    atomic_write_json,
    conversation_path,
    file_path,
    list_archived,
    load_archived,
    media_path,
    meta_path,
)


def test_conversation_and_meta_paths(tmp_path):
    assert conversation_path(tmp_path, "abc-123") == tmp_path / "conversations" / "abc-123.json"
    assert meta_path(tmp_path, "abc-123") == tmp_path / "conversations" / "abc-123.meta.json"
    assert media_path(tmp_path, "photo.png") == tmp_path / "media" / "photo.png"
    assert (
        file_path(tmp_path, "abc-123", "photo.png")
        == tmp_path / "files" / "abc-123" / "photo.png"
    )
    assert (
        artifact_path(tmp_path, "abc-123", "demo.py")
        == tmp_path / "artifacts" / "abc-123" / "demo.py"
    )


def test_atomic_write_json_roundtrips_and_replaces(tmp_path):
    target = tmp_path / "conversations" / "u.json"
    target.parent.mkdir()
    atomic_write_json(target, {"title": "first", "n": 1})
    atomic_write_json(target, {"title": "second"})
    assert json.loads(target.read_text()) == {"title": "second"}
    leftovers = [p.name for p in target.parent.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_list_archived_ignores_meta_and_other_files(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    atomic_write_json(conv / "one.json", {})
    atomic_write_json(conv / "two.json", {})
    atomic_write_json(conv / "one.meta.json", {})
    (conv / "stray.txt").write_text("x")
    assert list_archived(tmp_path) == {"one", "two"}


def test_load_archived_returns_sorted_payloads_and_filters(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    atomic_write_json(conv / "two.json", {"name": "two"})
    atomic_write_json(conv / "one.json", {"name": "one"})
    atomic_write_json(conv / "one.meta.json", {"media": {}})

    loaded = load_archived(tmp_path)

    assert [uuid for uuid, _ in loaded] == ["one", "two"]
    assert loaded[0][1] == {"name": "one"}
    assert load_archived(tmp_path, "two") == [("two", {"name": "two"})]
    assert load_archived(tmp_path, "missing") == []
