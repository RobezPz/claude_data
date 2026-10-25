import io
import json
import zipfile
from urllib.parse import parse_qs, urlsplit

from claude_data.files import DownloadError, Fetched
from claude_data.generated import (
    GeneratedResult,
    chunk_paths,
    download_generated_zips,
    extract_generated_paths,
    generated_url,
    process_archived_generated,
    update_meta_generated,
)
from claude_data.store import atomic_write_json, conversation_path, meta_path


def present_payload(*paths):
    return {
        "chat_messages": [
            {
                "content": [
                    {
                        "type": "tool_result",
                        "name": "present_files",
                        "content": [
                            {"type": "local_resource", "file_path": path}
                            for path in paths
                        ],
                    }
                ]
            }
        ]
    }


def make_zip(paths):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in paths:
            archive.writestr(path.rsplit("/", 1)[-1], f"content:{path}")
    return buffer.getvalue()


def test_extract_generated_paths_uses_present_files_and_dedupes():
    payload = {
        "chat_messages": [
            {
                "content": [
                    {
                        "type": "tool_result",
                        "name": "present_files",
                        "content": [
                            {"type": "local_resource", "file_path": "/out/a.py"},
                            {"type": "local_resource", "file_path": "/out/b.py"},
                            {"type": "local_resource", "file_path": "/out/a.py"},
                            {"type": "text", "text": "ignored"},
                        ],
                    },
                    {
                        "type": "tool_result",
                        "name": "SendUserFile",
                        "file_deliveries": [{"path": "/home/claude/x.tar.gz"}],
                    },
                ]
            }
        ]
    }

    assert extract_generated_paths(payload) == ["/out/a.py", "/out/b.py"]


def test_chunk_paths_limits_batch_size():
    paths = [f"/out/{index}.py" for index in range(17)]

    batches = chunk_paths(paths, size=8)

    assert [len(batch) for batch in batches] == [8, 8, 1]
    assert chunk_paths([], size=8) == []


def test_generated_url_repeats_paths_query_parameter():
    url = generated_url(
        "org-1", "conv-1", ["/out/a.py", "/out/b.py"]
    )

    parts = urlsplit(url)
    assert parts.path == (
        "/api/organizations/org-1/conversations/conv-1/wiggle/download-files"
    )
    assert parse_qs(parts.query)["paths"] == ["/out/a.py", "/out/b.py"]


def test_download_generated_zips_writes_zip_and_reports_entries(tmp_path):
    batches = [["/out/a.py", "/out/b.py"]]
    fetch_zip = lambda conversation, paths: Fetched(make_zip(paths), "application/zip")

    results = download_generated_zips(
        tmp_path, "conv-1", batches, fetch_zip
    )

    assert results[0].status == "downloaded"
    assert results[0].entries == 2
    target = tmp_path / results[0].local_path
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["a.py", "b.py"]


def test_download_generated_zips_skips_existing(tmp_path):
    target = tmp_path / "generated" / "conv-1" / "files-1.zip"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")

    def fail(conversation, paths):
        raise AssertionError("fetch must not run")

    results = download_generated_zips(
        tmp_path, "conv-1", [["/out/a.py"]], fail
    )

    assert results[0].status == "present"
    assert target.read_bytes() == b"old"


def test_download_generated_zips_respects_max_size(tmp_path):
    batches = [["/out/a.py"]]
    fetch_zip = lambda conversation, paths: Fetched(make_zip(paths), "application/zip")

    results = download_generated_zips(
        tmp_path, "conv-1", batches, fetch_zip, max_bytes=10
    )

    assert results[0].status == "skipped"
    assert not (tmp_path / "generated").exists()


def test_download_generated_zips_rejects_non_zip_and_records_failure(tmp_path):
    batches = [["/out/a.py"]]
    fetch_zip = lambda conversation, paths: Fetched(b"<html>", "text/html")

    results = download_generated_zips(
        tmp_path, "conv-1", batches, fetch_zip
    )

    assert results[0].status == "failed"
    assert "zip" in results[0].reason.lower()


def test_download_generated_zips_falls_back_when_batch_fails(tmp_path):
    batches = [["/out/a.py", "/out/b.py", "/out/c.py"]]

    def fetch_zip(conversation, paths):
        if len(paths) > 1:
            raise DownloadError("batch aborted")
        if paths[0].endswith("b.py"):
            raise DownloadError("HTTP 404")
        return Fetched(make_zip(paths), "application/zip")

    results = download_generated_zips(
        tmp_path, "conv-1", batches, fetch_zip
    )

    assert results[0].status == "downloaded"
    assert results[0].entries == 2
    assert results[0].missing == ["/out/b.py"]
    with zipfile.ZipFile(tmp_path / results[0].local_path) as archive:
        assert set(archive.namelist()) == {"a.py", "c.py"}


def test_generated_failure_reason_does_not_leak_exception_text(tmp_path):
    def fetch_zip(conversation, paths):
        raise RuntimeError("cookie=session-secret")

    results = download_generated_zips(
        tmp_path, "conv-1", [["/out/a.py"]], fetch_zip
    )

    assert "session-secret" not in results[0].reason
    assert "RuntimeError" in results[0].reason


def test_process_archived_generated_writes_zip_and_meta(tmp_path):
    payload = present_payload("/out/a.py", "/out/b.py")
    atomic_write_json(conversation_path(tmp_path, "conv-1"), payload)
    atomic_write_json(meta_path(tmp_path, "conv-1"), {"media": {}})
    fetch_zip = lambda conversation, paths: Fetched(make_zip(paths), "application/zip")

    results = process_archived_generated(
        tmp_path, [("conv-1", payload)], fetch_zip
    )

    assert results[0].status == "downloaded"
    meta = json.loads(meta_path(tmp_path, "conv-1").read_text())
    assert meta["media"] == {}
    assert meta["generated"][0]["status"] == "downloaded"
    assert meta["generated"][0]["file_count"] == 2


def test_process_archived_generated_dry_run_writes_nothing(tmp_path):
    payload = present_payload("/out/a.py")
    atomic_write_json(conversation_path(tmp_path, "conv-1"), payload)
    atomic_write_json(meta_path(tmp_path, "conv-1"), {"media": {}})

    def fail(conversation, paths):
        raise AssertionError("fetch must not run")

    results = process_archived_generated(
        tmp_path, [("conv-1", payload)], fail, dry_run=True
    )

    assert results[0].status == "planned"
    assert not (tmp_path / "generated").exists()


def test_update_meta_generated_records_success_and_failure():
    meta = {"media": {"u": "media/x"}}
    results = [
        GeneratedResult(
            conversation="conv-1",
            paths=["/out/a.py"],
            status="downloaded",
            local_path="generated/conv-1/files-1.zip",
            entries=1,
            size=100,
        ),
        GeneratedResult(
            conversation="conv-1",
            paths=["/out/b.py"],
            status="failed",
            reason="boom",
        ),
    ]

    updated = update_meta_generated(meta, results)

    assert updated["media"] == {"u": "media/x"}
    assert updated["generated"][0]["path"] == "generated/conv-1/files-1.zip"
    assert updated["generated"][0]["file_count"] == 1
    assert updated["generated_failures"] == [
        {"conversation": "conv-1", "paths": ["/out/b.py"], "reason": "boom"}
    ]


def test_update_meta_generated_replaces_stale_failures_and_records_missing():
    meta = {
        "generated_failures": [
            {"conversation": "conv-1", "paths": ["/out/old.py"], "reason": "old"}
        ]
    }
    results = [
        GeneratedResult(
            conversation="conv-1",
            paths=["/out/a.py", "/out/b.py"],
            status="downloaded",
            local_path="generated/conv-1/files-1.zip",
            entries=1,
            missing=["/out/b.py"],
        )
    ]

    updated = update_meta_generated(meta, results)

    assert updated["generated_failures"] == [
        {
            "conversation": "conv-1",
            "paths": ["/out/b.py"],
            "reason": "file not available",
        }
    ]


def test_update_meta_generated_preserves_missing_paths_on_present_zip():
    meta = {
        "generated": [
            {
                "path": "generated/conv-1/files-1.zip",
                "status": "downloaded",
                "missing_paths": ["/out/b.py"],
            }
        ]
    }
    results = [
        GeneratedResult(
            conversation="conv-1",
            paths=["/out/a.py", "/out/b.py"],
            status="present",
            local_path="generated/conv-1/files-1.zip",
        )
    ]

    updated = update_meta_generated(meta, results)

    assert updated["generated"][0]["missing_paths"] == ["/out/b.py"]
    assert updated["generated_failures"] == [
        {
            "conversation": "conv-1",
            "paths": ["/out/b.py"],
            "reason": "file not available",
        }
    ]
