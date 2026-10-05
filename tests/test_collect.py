import json
from pathlib import Path

from claude_data.collect import ConversationRef, parse_list_response

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "list_response.json"


def test_parse_list_response_returns_refs():
    obj = json.loads(FIXTURE_PATH.read_text())
    refs = parse_list_response(obj)
    assert refs == [
        ConversationRef(
            uuid="3a59ff1e-dae3-5633-bead-42e1f40eb371",
            title="Sample conversation A",
            created_at="2026-10-01T12:00:00Z",
            updated_at="2026-10-01T12:00:00Z",
        ),
        ConversationRef(
            uuid="58df5430-b5b9-53fb-b54d-4ae9b5c09761",
            title="Sample conversation B",
            created_at="2026-10-02T12:00:00Z",
            updated_at="2026-10-02T12:00:00Z",
        ),
    ]


def test_parse_list_response_empty_and_alien():
    assert parse_list_response({}) == []
    assert parse_list_response({"unrelated": []}) == []
