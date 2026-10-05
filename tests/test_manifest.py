import json
from datetime import datetime
from pathlib import Path

from claude_data.store import build_manifest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "conversation.json"


def test_build_manifest_from_disk(tmp_path):
    root = tmp_path
    (root / "conversations").mkdir()
    payload = json.loads(FIXTURE_PATH.read_text())
    uuid = "3a59ff1e-dae3-5633-bead-42e1f40eb371"
    (root / "conversations" / f"{uuid}.json").write_text(json.dumps(payload))
    (root / "conversations" / f"{uuid}.meta.json").write_text(
        json.dumps(
            {
                "media": {"https://cdn.example.com/f/1.png": "media/1.png"},
                "media_failures": ["https://cdn.example.com/f/2.png"],
            }
        )
    )
    (root / "conversations" / "1a59ff1e-dae3-5633-bead-42e1f40eb371.json").write_text(
        json.dumps({"name": "no sidecar"})
    )

    manifest = build_manifest(root)

    assert [entry["uuid"] for entry in manifest["conversations"]] == sorted(
        ["1a59ff1e-dae3-5633-bead-42e1f40eb371", uuid]
    )
    entry = next(e for e in manifest["conversations"] if e["uuid"] == uuid)
    assert entry == {
        "uuid": uuid,
        "title": "Sample conversation title",
        "created_at": "2026-10-01T12:00:05Z",
        "updated_at": "2026-10-01T12:00:05Z",
        "message_count": 2,
        "source_url": f"https://claude.ai/chat/{uuid}",
        "media": {"https://cdn.example.com/f/1.png": "media/1.png"},
        "media_failures": ["https://cdn.example.com/f/2.png"],
    }
    bare = next(
        e for e in manifest["conversations"] if e["uuid"] == "1a59ff1e-dae3-5633-bead-42e1f40eb371"
    )
    assert bare["media"] == {}
    assert bare["media_failures"] == []
    generated_at = manifest["generated_at"]
    parsed = datetime.fromisoformat(generated_at)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_build_manifest_empty_root(tmp_path):
    manifest = build_manifest(tmp_path)
    assert manifest["conversations"] == []
    assert manifest["generated_at"]