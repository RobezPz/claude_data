import json
import threading
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import quote

import pytest

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


def test_unknown_path_404(server):
    _, url = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{url}/api/nope")
    assert exc.value.code == 404
