import json
from pathlib import Path

from claude_data.media import extract_media_urls
from claude_data.store import extract_metadata

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "conversation.json"


def test_extract_metadata_from_fixture():
    payload = json.loads(FIXTURE_PATH.read_text())
    assert extract_metadata(payload) == {
        "title": "Sample conversation title",
        "created_at": "2026-10-01T12:00:05Z",
        "updated_at": "2026-10-01T12:00:05Z",
        "message_count": 2,
    }


def test_extract_metadata_empty_payload():
    assert extract_metadata({}) == {
        "title": "",
        "created_at": "",
        "updated_at": "",
        "message_count": 0,
    }


def test_extract_metadata_partial_payload():
    assert extract_metadata({"name": "only a title"}) == {
        "title": "only a title",
        "created_at": "",
        "updated_at": "",
        "message_count": 0,
    }


def test_extract_media_urls_from_fixture():
    payload = json.loads(FIXTURE_PATH.read_text())
    assert extract_media_urls(payload) == []


def test_extract_media_urls_keeps_media_only_and_dedupes():
    payload = {
        "chat_messages": [
            {
                "content": [
                    {
                        "type": "text",
                        "text": "see image and link",
                        "citations": [
                            {
                                "url": "https://cdn.example.com/shot.png",
                                "sources": [
                                    {"url": "https://cdn.example.com/shot.png"},
                                    {"url": "https://example.com/article"},
                                ],
                                "metadata": {
                                    "favicon_url": "https://www.google.com/s2/favicons?domain=example.com"
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }
    assert extract_media_urls(payload) == ["https://cdn.example.com/shot.png"]
    payload["chat_messages"][0]["content"][0]["citations"][0]["url"] = (
        "https://claudeusercontent.com/pic.png"
    )
    assert extract_media_urls(payload) == [
        "https://claudeusercontent.com/pic.png",
        "https://cdn.example.com/shot.png",
    ]


def test_extract_media_urls_alien_payload():
    assert extract_media_urls({"x": 1}) == []