import json

import pytest

from claude_data.archiver.fetch import CaptureError, process_conversation


def test_process_conversation_writes_rewritten_payload_and_meta(tmp_path):
    payload = {"title": "t", "msgs": [{"text": "see https://cdn.example.com/a/1.png"}]}
    capture = lambda uuid: payload
    download = lambda pairs: ({"https://cdn.example.com/a/1.png": "media/1.png"}, [])
    process_conversation(tmp_path, "uuid-1", capture, download)
    saved = json.loads((tmp_path / "conversations" / "uuid-1.json").read_text())
    assert saved["msgs"][0]["text"] == "see media/1.png"
    meta = json.loads((tmp_path / "conversations" / "uuid-1.meta.json").read_text())
    assert meta == {
        "media": {"https://cdn.example.com/a/1.png": "media/1.png"},
        "media_failures": [],
    }


def test_process_conversation_partial_media_failure(tmp_path):
    payload = {"m": ["https://cdn.example.com/a/1.png", "https://cdn.example.com/a/2.png"]}
    download = lambda pairs: (
        {"https://cdn.example.com/a/1.png": "media/1.png"},
        ["https://cdn.example.com/a/2.png"],
    )
    process_conversation(tmp_path, "u", lambda uuid: payload, download)
    saved = json.loads((tmp_path / "conversations" / "u.json").read_text())
    assert saved["m"] == ["media/1.png", "https://cdn.example.com/a/2.png"]
    meta = json.loads((tmp_path / "conversations" / "u.meta.json").read_text())
    assert meta["media_failures"] == ["https://cdn.example.com/a/2.png"]


def test_process_conversation_capture_none_raises(tmp_path):
    with pytest.raises(CaptureError):
        process_conversation(tmp_path, "u", lambda uuid: None, lambda pairs: ({}, []))


def test_process_conversation_skips_download_for_present_media(tmp_path):
    media = tmp_path / "media"
    media.mkdir(parents=True)
    (media / "1.png").write_bytes(b"already here")
    payload = {"m": ["https://cdn.example.com/a/1.png"]}
    calls: list[list[tuple[str, str]]] = []
    process_conversation(tmp_path, "u", lambda uuid: payload, calls.append)
    assert calls == []
    saved = json.loads((tmp_path / "conversations" / "u.json").read_text())
    assert saved["m"] == ["media/1.png"]