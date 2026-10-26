import io
import json
import zipfile

import pytest

import claude_data
from claude_data import parse_args, run_sync
from claude_data.archiver.env import MissingUsernameError
from claude_data.archiver.fetch import FetchReport


def conversation_payload(uuid: str = "conv-1", with_generated: bool = True) -> dict:
    messages = [
        {
            "uuid": "m1",
            "files": [
                {
                    "file_uuid": f"file-{uuid}",
                    "file_name": "photo.png",
                    "file_kind": "image",
                    "preview_url": f"/api/org-1/files/file-{uuid}/preview",
                }
            ],
        },
        {
            "uuid": "m2",
            "content": [
                {
                    "type": "tool_use",
                    "name": "artifacts",
                    "input": {
                        "id": f"art-{uuid}",
                        "title": "Note",
                        "type": "text/markdown",
                        "language": "markdown",
                        "content": "# hi",
                    },
                }
            ],
        },
    ]
    if with_generated:
        messages.append(
            {
                "uuid": "m3",
                "content": [
                    {
                        "type": "tool_result",
                        "name": "present_files",
                        "content": [
                            {"type": "local_resource", "file_path": f"/mnt/{uuid}.txt"}
                        ],
                    }
                ],
            }
        )
    return {"name": "C", "chat_messages": messages}


def write_conversation(root, uuid: str, payload: dict) -> None:
    conversations = root / "conversations"
    conversations.mkdir(parents=True, exist_ok=True)
    (conversations / f"{uuid}.json").write_text(json.dumps(payload))


@pytest.fixture
def sync_archive(tmp_path):
    root = tmp_path / "archive"
    write_conversation(root, "conv-1", conversation_payload())
    return root


class FakeResponse:
    def __init__(self, body=b"img", content_type="image/webp", ok=True, status=200):
        self._body = body
        self.headers = {"content-type": content_type}
        self.ok = ok
        self.status = status

    def body(self):
        return self._body


def zip_payload() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("out.txt", "hello")
    return buffer.getvalue()


class FakeContext:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.urls: list[str] = []
        self.closed = False

    @property
    def request(self):
        return self

    def get(self, url):
        self.urls.append(url)
        if not self.ok:
            return FakeResponse(ok=False, status=500)
        if "download-files" in url:
            return FakeResponse(zip_payload(), "application/zip")
        return FakeResponse()

    def close(self):
        self.closed = True


def run_sync_argv(monkeypatch, root, argv, context_factory=None):
    monkeypatch.setattr(claude_data, "ARCHIVE_ROOT", root)
    monkeypatch.setattr(claude_data, "read_org", lambda page: "org-1")
    monkeypatch.setattr(claude_data, "collect_conversations", lambda page: [])
    monkeypatch.setattr(
        claude_data,
        "fetch_all",
        lambda *args, **kwargs: FetchReport(fetched=[], failed=[], skipped=0),
    )
    if context_factory is None:
        context_factory = lambda force_login, show_browser: (FakeContext(), object())
    with pytest.raises(SystemExit) as excinfo:
        run_sync(parse_args(argv), context_factory)
    return excinfo.value.code


def test_run_sync_downloads_files_generated_and_artifacts(
    monkeypatch, sync_archive, capsys
):
    context = FakeContext()
    calls = []

    def factory(force_login, show_browser):
        calls.append((force_login, show_browser))
        return context, object()

    code = run_sync_argv(monkeypatch, sync_archive, ["sync"], factory)

    assert code == 0
    assert calls == [(False, False)]
    assert context.closed is True
    assert (
        sync_archive / "files" / "conv-1" / "file-conv-1-photo.webp"
    ).read_bytes() == b"img"
    assert (sync_archive / "artifacts" / "conv-1" / "art-conv-1.md").read_text() == "# hi"
    assert zipfile.is_zipfile(sync_archive / "generated" / "conv-1" / "files-1.zip")
    assert (sync_archive / "index.json").is_file()
    assert any("download-files" in url for url in context.urls)
    out = capsys.readouterr().out
    assert "conversations:" in out and "files:" in out and "artifacts:" in out


def test_run_sync_forwards_login_and_show_browser(monkeypatch, sync_archive):
    calls = []

    def factory(force_login, show_browser):
        calls.append((force_login, show_browser))
        return FakeContext(), object()

    code = run_sync_argv(
        monkeypatch, sync_archive, ["sync", "--login", "--show-browser"], factory
    )

    assert code == 0
    assert calls == [(True, True)]


def test_run_sync_second_run_downloads_nothing(monkeypatch, sync_archive):
    run_sync_argv(monkeypatch, sync_archive, ["sync"])

    second = FakeContext()
    code = run_sync_argv(
        monkeypatch, sync_archive, ["sync"], lambda *args: (second, object())
    )

    assert code == 0
    assert second.urls == []


def test_run_sync_force_redownloads(monkeypatch, sync_archive):
    run_sync_argv(monkeypatch, sync_archive, ["sync"])

    forced = FakeContext()
    code = run_sync_argv(
        monkeypatch, sync_archive, ["sync", "--force"], lambda *args: (forced, object())
    )

    assert code == 0
    assert any("download-files" in url for url in forced.urls)
    assert any("/files/file-conv-1/preview" in url for url in forced.urls)


def test_run_sync_dry_run_plans_without_browser_or_writes(
    monkeypatch, sync_archive, capsys
):
    def factory(*args):
        raise AssertionError("browser launched during dry run")

    code = run_sync_argv(monkeypatch, sync_archive, ["sync", "--dry-run"], factory)

    assert code == 0
    assert not (sync_archive / "files").exists()
    assert not (sync_archive / "artifacts").exists()
    assert not (sync_archive / "generated").exists()
    assert not (sync_archive / "index.json").exists()
    assert "planned" in capsys.readouterr().out


def test_run_sync_reports_failures_with_exit_1(monkeypatch, sync_archive, capsys):
    code = run_sync_argv(
        monkeypatch,
        sync_archive,
        ["sync"],
        lambda *args: (FakeContext(ok=False), object()),
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "file issues" in err


def test_run_sync_unknown_conversation_exits_2(monkeypatch, sync_archive, capsys):
    def factory(*args):
        raise AssertionError("browser launched for an unknown conversation")

    code = run_sync_argv(
        monkeypatch, sync_archive, ["sync", "--conversation", "missing"], factory
    )

    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_run_sync_missing_username_exits_2(monkeypatch, sync_archive, capsys):
    def factory(*args):
        raise MissingUsernameError("CLAUDE_USERNAME missing from .env")

    code = run_sync_argv(monkeypatch, sync_archive, ["sync"], factory)

    assert code == 2
    assert "CLAUDE_USERNAME" in capsys.readouterr().err


def test_run_sync_conversation_scopes_file_passes(monkeypatch, sync_archive):
    write_conversation(sync_archive, "conv-2", conversation_payload("conv-2"))

    code = run_sync_argv(
        monkeypatch, sync_archive, ["sync", "--conversation", "conv-1"]
    )

    assert code == 0
    assert (sync_archive / "files" / "conv-1" / "file-conv-1-photo.webp").is_file()
    assert not (sync_archive / "files" / "conv-2").exists()
    assert not (sync_archive / "artifacts" / "conv-2").exists()


def test_run_sync_closes_browser_before_artifacts_and_manifest(monkeypatch, tmp_path):
    events: list[str] = []

    class RecordingContext(FakeContext):
        def close(self):
            events.append("close")
            super().close()

    monkeypatch.setattr(claude_data, "ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr(
        claude_data, "collect_conversations", lambda page: events.append("collect") or []
    )
    monkeypatch.setattr(
        claude_data,
        "fetch_all",
        lambda *args, **kwargs: events.append("fetch")
        or FetchReport(fetched=[], failed=[], skipped=0),
    )
    monkeypatch.setattr(
        claude_data, "load_archived", lambda root, only=None: events.append("archive") or []
    )
    monkeypatch.setattr(
        claude_data, "read_org", lambda page: events.append("org") or "org-1"
    )
    monkeypatch.setattr(
        claude_data,
        "process_archived_files",
        lambda *args, **kwargs: events.append("files") or [],
    )
    monkeypatch.setattr(
        claude_data,
        "process_archived_generated",
        lambda *args, **kwargs: events.append("generated") or [],
    )
    monkeypatch.setattr(
        claude_data,
        "process_archived_artifacts",
        lambda *args, **kwargs: events.append("artifacts") or [],
    )

    def record_index(path, obj):
        if path.name == "index.json":
            events.append("index")

    monkeypatch.setattr(claude_data, "atomic_write_json", record_index)

    def factory(force_login, show_browser):
        events.append("open")
        return RecordingContext(), object()

    with pytest.raises(SystemExit) as excinfo:
        run_sync(parse_args(["sync"]), factory)

    assert excinfo.value.code == 0
    assert events == [
        "open",
        "collect",
        "fetch",
        "archive",
        "org",
        "files",
        "generated",
        "close",
        "artifacts",
        "index",
    ]


def test_run_sync_missing_org_skips_generated_and_exits_1(
    monkeypatch, sync_archive, capsys
):
    monkeypatch.setattr(claude_data, "ARCHIVE_ROOT", sync_archive)
    monkeypatch.setattr(claude_data, "read_org", lambda page: None)
    monkeypatch.setattr(claude_data, "collect_conversations", lambda page: [])
    monkeypatch.setattr(
        claude_data,
        "fetch_all",
        lambda *args, **kwargs: FetchReport(fetched=[], failed=[], skipped=0),
    )
    generated_calls = []
    monkeypatch.setattr(
        claude_data,
        "process_archived_generated",
        lambda *args, **kwargs: generated_calls.append(args) or [],
    )

    with pytest.raises(SystemExit) as excinfo:
        run_sync(
            parse_args(["sync"]), lambda *args: (FakeContext(), object())
        )

    assert excinfo.value.code == 1
    assert generated_calls == []
    assert "lastActiveOrg" in capsys.readouterr().err
