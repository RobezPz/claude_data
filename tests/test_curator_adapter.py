from curator_fixtures import message, write_conversation  # pyrefly: ignore[missing-import]

from claude_data.curator.adapters import ClaudeAdapter


def test_load_builds_source_and_messages(tmp_path):
    payload = {
        "uuid": "conv-1",
        "name": "My conversation",
        "model": "claude",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            message("h1", "human", 0, text="q"),
            message("a1", "assistant", 1, parent="h1", text="a"),
        ],
    }
    path = write_conversation(tmp_path, payload)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert bundle.source.source_id == "claude:conv-1"
    assert bundle.source.title == "My conversation"
    assert bundle.leaf_uuid == "a1"
    assert [m.role for m in bundle.messages] == ["human", "assistant"]


def test_asset_normalization_covers_all_kinds(tmp_path):
    (tmp_path / "files" / "conv-1").mkdir(parents=True)
    (tmp_path / "files" / "conv-1" / "photo.webp").write_bytes(b"x")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "paper.pdf").write_bytes(b"x")
    (tmp_path / "artifacts" / "conv-1").mkdir(parents=True)
    (tmp_path / "artifacts" / "conv-1" / "demo.py").write_text("print(1)")
    (tmp_path / "generated" / "conv-1").mkdir(parents=True)
    (tmp_path / "generated" / "conv-1" / "files-1.zip").write_bytes(b"x")

    payload = {
        "uuid": "conv-1",
        "name": "T",
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            {
                **message("h1", "human", 0, text="see https://example.com/a.pdf"),
                "files": [
                    {
                        "file_uuid": "f1",
                        "file_name": "photo.png",
                        "file_kind": "image",
                        "local_path": "files/conv-1/photo.webp",
                    }
                ],
            },
            {
                **message("a1", "assistant", 1, parent="h1"),
                "content": [
                    {
                        "type": "tool_use",
                        "name": "artifacts",
                        "input": {"id": "demo", "title": "Demo"},
                    },
                    {
                        "type": "tool_result",
                        "name": "present_files",
                        "content": [
                            {"type": "local_resource", "file_path": "/out/report.txt"}
                        ],
                    },
                ],
            },
        ],
    }
    meta = {
        "media": {"https://example.com/a.pdf": "media/paper.pdf"},
        "artifacts": {"demo": {"path": "artifacts/conv-1/demo.py"}},
        "generated": [
            {
                "path": "generated/conv-1/files-1.zip",
                "source_paths": ["/out/report.txt"],
            }
        ],
    }
    path = write_conversation(tmp_path, payload, meta)
    bundle = ClaudeAdapter().load(path, tmp_path)
    kinds = {a.kind for m in bundle.messages for a in m.assets}
    assert kinds == {"image", "media", "artifact", "generated"}
    assert bundle.source.assets == []


def test_media_asset_matches_rewritten_local_path(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "paper.pdf").write_bytes(b"x")
    payload = {
        "uuid": "conv-1",
        "chat_messages": [
            message("h1", "human", 0, text="see media/paper.pdf")
        ],
        "current_leaf_message_uuid": "h1",
    }
    meta = {"media": {"https://example.com/a.pdf": "media/paper.pdf"}}
    path = write_conversation(tmp_path, payload, meta)
    bundle = ClaudeAdapter().load(path, tmp_path)
    media = [asset for asset in bundle.messages[0].assets if asset.kind == "media"]
    assert media and media[0].local_path == "media/paper.pdf"


def test_unmatched_generated_zip_is_source_level(tmp_path):
    (tmp_path / "generated" / "conv-1").mkdir(parents=True)
    (tmp_path / "generated" / "conv-1" / "files-1.zip").write_bytes(b"x")
    payload = {
        "uuid": "conv-1",
        "chat_messages": [message("h1", "human", 0)],
        "current_leaf_message_uuid": "h1",
    }
    meta = {
        "generated": [
            {
                "path": "generated/conv-1/files-1.zip",
                "source_paths": ["/out/missing.txt"],
            }
        ]
    }
    path = write_conversation(tmp_path, payload, meta)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert [a.kind for a in bundle.source.assets] == ["generated"]


def test_missing_meta_is_tolerated(tmp_path):
    payload = {
        "uuid": "conv-1",
        "chat_messages": [message("h1", "human", 0)],
        "current_leaf_message_uuid": "h1",
    }
    path = write_conversation(tmp_path, payload)
    bundle = ClaudeAdapter().load(path, tmp_path)
    assert bundle.messages[0].assets == []
