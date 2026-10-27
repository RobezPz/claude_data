import json
import threading
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import quote

import pytest
from curator_fixtures import message, write_conversation  # pyrefly: ignore[missing-import]

from claude_data.archiver.store import atomic_write_json
from claude_data.curator.server import create_server


def get_json(url):
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def put_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


@pytest.fixture
def server(curator_archive, tmp_path):
    srv = create_server(curator_archive, tmp_path / "curated", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_library_endpoint(server):
    _, url = server
    data = get_json(f"{url}/api/library")
    assert data["pairs"][0]["pair_id"].startswith("claude:conv-1:")


def test_pair_endpoint(server):
    _, url = server
    pair_id = get_json(f"{url}/api/library")["pairs"][0]["pair_id"]
    data = get_json(f"{url}/api/pairs/{quote(pair_id)}")
    assert "question_html" in data and "answer_html" in data


def test_state_put_and_conflict(server, curator_collection):
    _, url = server
    state = get_json(f"{url}/api/state")
    payload = {
        "expected_revision": state["revision"],
        "collections": [asdict(curator_collection)],
    }
    saved = put_json(f"{url}/api/state", payload)
    assert saved["revision"] == state["revision"] + 1
    with pytest.raises(urllib.error.HTTPError) as exc:
        put_json(f"{url}/api/state", payload)
    assert exc.value.code == 409


def test_assets_served_and_traversal_rejected(server, curator_archive, tmp_path):
    _, url = server
    with urllib.request.urlopen(f"{url}/assets/files/conv-1/photo.webp") as response:
        assert response.read() == b"image"
    (tmp_path / "secret.txt").write_text("secret")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/assets/%2e%2e/secret.txt")
    assert exc.value.code == 404


def test_export_zip_endpoint(server, curator_collection):
    _, url = server
    state = get_json(f"{url}/api/state")
    put_json(
        f"{url}/api/state",
        {
            "expected_revision": state["revision"],
            "collections": [asdict(curator_collection)],
        },
    )
    with urllib.request.urlopen(f"{url}/api/export/c1.zip") as response:
        assert response.headers["Content-Type"] == "application/zip"
        assert response.read()[:2] == b"PK"


def test_reload_endpoint(server):
    _, url = server
    request = urllib.request.Request(
        f"{url}/api/reload",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        assert json.load(response)["pairs"] >= 1


def _new_conversation(archive, uuid):
    payload = {
        "uuid": uuid,
        "name": f"Conversation {uuid}",
        "model": "m",
        "created_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "current_leaf_message_uuid": f"{uuid}-a1",
        "chat_messages": [
            message(f"{uuid}-h1", "human", 0, text="new question"),
            message(f"{uuid}-a1", "assistant", 1, parent=f"{uuid}-h1", text="new answer"),
        ],
    }
    write_conversation(archive, payload)
    return payload


def test_library_endpoint_picks_up_a_conversation_added_after_start(
    server, curator_archive
):
    srv, url = server
    assert len(get_json(f"{url}/api/library")["sources"]) == 1

    _new_conversation(curator_archive, "conv-2")

    sources = get_json(f"{url}/api/library")["sources"]
    assert "claude:conv-2" in {source["source_id"] for source in sources}
    assert len(srv.library.sources) == 2


def test_pair_endpoint_serves_a_conversation_added_after_start(server, curator_archive):
    _, url = server
    _new_conversation(curator_archive, "conv-2")
    pair_id = next(
        pair["pair_id"]
        for pair in get_json(f"{url}/api/library")["pairs"]
        if pair["source_id"] == "claude:conv-2"
    )

    data = get_json(f"{url}/api/pairs/{quote(pair_id)}")

    assert data["pair"]["source_id"] == "claude:conv-2"


def test_library_endpoint_picks_up_a_rewritten_conversation(server, curator_archive):
    _, url = server
    assert get_json(f"{url}/api/library")["sources"][0]["title"] == "T"

    path = curator_archive / "conversations" / "conv-1.json"
    payload = json.loads(path.read_text())
    payload["name"] = "Renamed"
    atomic_write_json(path, payload)

    titles = {
        source["source_id"]: source["title"]
        for source in get_json(f"{url}/api/library")["sources"]
    }
    assert titles["claude:conv-1"] == "Renamed"


def test_library_is_cached_while_the_archive_is_unchanged(server):
    srv, url = server
    get_json(f"{url}/api/library")
    first = srv.library
    get_json(f"{url}/api/library")
    assert srv.library is first


def test_unknown_path_404(server):
    _, url = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/api/nope")
    assert exc.value.code == 404


def test_state_rejects_unsafe_collection_id(server, curator_collection):
    _, url = server
    state = get_json(f"{url}/api/state")
    collection = asdict(curator_collection)
    collection["collection_id"] = "../evil"
    with pytest.raises(urllib.error.HTTPError) as exc:
        put_json(
            f"{url}/api/state",
            {
                "expected_revision": state["revision"],
                "collections": [collection],
            },
        )
    assert exc.value.code == 400


def test_state_rejects_bad_json(server):
    _, url = server
    request = urllib.request.Request(
        f"{url}/api/state",
        data=b"{bad",
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400


def test_malformed_state_returns_json_error(server, tmp_path):
    _, url = server
    data_dir = tmp_path / "curated"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "collections.json").write_text("{bad")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/api/state")
    assert exc.value.code == 500
    body = json.loads(exc.value.read())
    assert "collections.json.bak" in body["error"]
