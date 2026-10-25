import json

from claude_data.files import (
    DownloadError,
    Fetched,
    FileRef,
    FileResult,
    apply_file_results,
    download_files,
    extract_file_refs,
    local_filename,
    plan_file_downloads,
    process_archived_files,
    resolve_url,
    safe_filename,
    update_meta,
)
from claude_data.store import atomic_write_json, conversation_path, meta_path


def make_ref(**overrides):
    defaults = {
        "conversation": "conv-1",
        "file_uuid": "file-1",
        "file_name": "photo.png",
        "kind": "image",
        "size_bytes": 1000,
        "url": "/api/org/files/file-1/preview",
        "variant": "preview",
        "reason": None,
    }
    defaults.update(overrides)
    return FileRef(**defaults)


def payload_with(*messages):
    return {"chat_messages": list(messages)}


def test_resolve_url_adds_claude_origin_for_relative_paths():
    assert (
        resolve_url("/api/org/files/u/preview")
        == "https://claude.ai/api/org/files/u/preview"
    )
    assert (
        resolve_url("https://claude.ai/api/org/files/u/preview")
        == "https://claude.ai/api/org/files/u/preview"
    )


def test_safe_filename_strips_directories_and_handles_empty():
    assert safe_filename("../../etc/passwd.png", "fallback") == "passwd.png"
    assert safe_filename("..\\..\\evil.png", "fallback") == "evil.png"
    assert safe_filename("", "fallback") == "fallback"
    assert safe_filename("..", "fallback") == "fallback"


def test_local_filename_uses_webp_for_previews_and_keeps_original_extension():
    assert local_filename(make_ref(file_name="photo.png")) == "file-1-photo.webp"
    assert (
        local_filename(make_ref(file_name="paper.pdf", variant="original"))
        == "file-1-paper.pdf"
    )
    assert local_filename(make_ref(file_name="")) == "file-1.webp"


def test_extract_file_refs_prefers_document_original():
    payload = payload_with(
        {
            "files": [
                {
                    "file_uuid": "doc-1",
                    "file_name": "paper.pdf",
                    "file_kind": "document",
                    "size_bytes": 1234,
                    "document_asset": {
                        "url": "/api/org/files/doc-1/document_pdf",
                        "file_variant": "original",
                    },
                    "preview_url": "/api/org/files/doc-1/preview",
                }
            ]
        }
    )

    refs = extract_file_refs("conv-1", payload)

    assert len(refs) == 1
    assert refs[0].url == "/api/org/files/doc-1/document_pdf"
    assert refs[0].variant == "original"
    assert refs[0].kind == "document"


def test_extract_file_refs_uses_preview_for_images():
    payload = payload_with(
        {
            "files": [
                {
                    "file_uuid": "img-1",
                    "file_name": "pic.jpg",
                    "file_kind": "image",
                    "size_bytes": 500,
                    "preview_url": "/api/org/files/img-1/preview",
                    "thumbnail_url": "/api/org/files/img-1/thumbnail",
                }
            ]
        }
    )

    refs = extract_file_refs("conv-1", payload)

    assert len(refs) == 1
    assert refs[0].url == "/api/org/files/img-1/preview"
    assert refs[0].variant == "preview"


def test_extract_file_refs_marks_blob_without_url_unavailable():
    payload = payload_with(
        {
            "files": [
                {
                    "file_uuid": "blob-1",
                    "file_name": "data.csv",
                    "file_kind": "blob",
                    "size_bytes": 10,
                    "path": "/mnt/user-data/uploads/data.csv",
                }
            ]
        }
    )

    refs = extract_file_refs("conv-1", payload)

    assert len(refs) == 1
    assert refs[0].url is None
    assert refs[0].reason


def test_extract_file_refs_dedupes_by_uuid_across_messages():
    entry = {
        "files": [
            {
                "file_uuid": "img-1",
                "file_name": "pic.jpg",
                "file_kind": "image",
                "size_bytes": 500,
                "preview_url": "/api/org/files/img-1/preview",
            }
        ]
    }
    payload = payload_with(entry, {"files": [dict(entry["files"][0])]})

    refs = extract_file_refs("conv-1", payload)

    assert len(refs) == 1


def test_plan_file_downloads_skips_existing_file(tmp_path):
    ref = make_ref()
    first = plan_file_downloads([ref], tmp_path)
    target = first.to_download[0].local_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"already here")

    plan = plan_file_downloads([ref], tmp_path)

    assert plan.to_download == []
    assert plan.results[0].status == "present"
    assert plan.results[0].local_path


def test_plan_file_downloads_applies_max_size_to_original_only(tmp_path):
    original = make_ref(
        file_uuid="doc-1",
        file_name="big.pdf",
        kind="document",
        size_bytes=2_000_000,
        url="/api/org/files/doc-1/document_pdf",
        variant="original",
    )
    preview = make_ref(
        file_uuid="img-1",
        file_name="big.jpg",
        size_bytes=2_000_000,
        variant="preview",
    )

    plan = plan_file_downloads([original, preview], tmp_path, max_bytes=1_000_000)

    assert [p.ref.file_uuid for p in plan.to_download] == ["img-1"]
    skipped = [r for r in plan.results if r.ref.file_uuid == "doc-1"]
    assert skipped and skipped[0].status == "skipped"
    assert "max" in skipped[0].reason.lower()


def test_plan_file_downloads_records_unavailable_refs(tmp_path):
    ref = make_ref(
        file_uuid="blob-1",
        file_name="data.csv",
        kind="blob",
        url=None,
        variant=None,
        reason="no downloadable URL",
    )

    plan = plan_file_downloads([ref], tmp_path)

    assert plan.to_download == []
    assert plan.results[0].status == "unavailable"
    assert plan.results[0].reason == "no downloadable URL"


def test_download_files_writes_bytes_and_reports_success(tmp_path):
    ref = make_ref()
    plan = plan_file_downloads([ref], tmp_path)
    planned = plan.to_download[0]

    results = download_files(
        plan, lambda url: Fetched(b"image-bytes", "image/webp"), tmp_path
    )

    assert results[0].status == "downloaded"
    assert results[0].size == len(b"image-bytes")
    assert planned.local_path.read_bytes() == b"image-bytes"


def test_download_files_skips_body_over_max_size(tmp_path):
    ref = make_ref()
    plan = plan_file_downloads([ref], tmp_path)
    planned = plan.to_download[0]

    results = download_files(
        plan, lambda url: Fetched(b"x" * 2000, "image/webp"), tmp_path, max_bytes=1000
    )

    assert results[0].status == "skipped"
    assert "max" in results[0].reason.lower()
    assert not planned.local_path.exists()


def test_download_files_records_fetch_failure(tmp_path):
    ref = make_ref()
    plan = plan_file_downloads([ref], tmp_path)

    def fail(url):
        raise DownloadError("boom")

    results = download_files(plan, fail, tmp_path)

    assert results[0].status == "failed"
    assert "boom" in results[0].reason


def test_download_files_hides_unexpected_exception_text(tmp_path):
    ref = make_ref()
    plan = plan_file_downloads([ref], tmp_path)

    def fail(url):
        raise RuntimeError("cookie=session-secret")

    results = download_files(plan, fail, tmp_path)

    assert "session-secret" not in results[0].reason
    assert "RuntimeError" in results[0].reason


def test_apply_file_results_adds_local_path_without_rewriting_urls(tmp_path):
    preview = "/api/org/files/img-1/preview"
    thumbnail = "/api/org/files/img-1/thumbnail"
    payload = {
        "files": [
            {
                "file_uuid": "img-1",
                "preview_url": preview,
                "preview_asset": {"url": preview},
                "thumbnail_url": thumbnail,
            }
        ]
    }
    ref = make_ref(file_uuid="img-1", url=preview)

    applied = apply_file_results(
        payload,
        [FileResult(ref=ref, status="downloaded", local_path="files/conv-1/img-1.webp")],
    )

    assert applied["files"][0]["local_path"] == "files/conv-1/img-1.webp"
    assert applied["files"][0]["preview_url"] == preview
    assert applied["files"][0]["preview_asset"]["url"] == preview
    assert payload["files"][0].get("local_path") is None


def test_update_meta_preserves_media_and_records_file_status(tmp_path):
    meta = {"media": {"https://x/y.pdf": "media/y.pdf"}, "media_failures": ["z"]}
    ref = make_ref()
    results = [
        FileResult(ref=ref, status="downloaded", local_path="files/conv-1/img-1.png"),
        FileResult(
            ref=make_ref(file_uuid="blob-1", url=None, variant=None),
            status="unavailable",
            reason="no downloadable URL",
        ),
    ]

    updated = update_meta(meta, results)

    assert updated["media"] == meta["media"]
    assert updated["media_failures"] == ["z"]
    assert updated["files"]["file-1"]["path"] == "files/conv-1/img-1.png"
    assert updated["files"]["file-1"]["status"] == "downloaded"
    assert updated["file_failures"] == [
        {"file_uuid": "blob-1", "reason": "no downloadable URL"}
    ]


def _write_conversation(tmp_path, conversation, payload, meta=None):
    atomic_write_json(conversation_path(tmp_path, conversation), payload)
    atomic_write_json(meta_path(tmp_path, conversation), meta or {})


def test_process_archived_files_downloads_rewrites_and_updates_meta(tmp_path):
    conversation = "conv-1"
    ref = make_ref()
    payload = {
        "chat_messages": [
            {
                "files": [
                    {
                        "file_uuid": "file-1",
                        "file_name": "photo.png",
                        "file_kind": "image",
                        "size_bytes": 1000,
                        "preview_url": ref.url,
                    }
                ]
            }
        ]
    }
    _write_conversation(tmp_path, conversation, payload, {"media": {}})

    results = process_archived_files(
        tmp_path,
        [(conversation, payload)],
        lambda url: Fetched(b"image-bytes", "image/webp"),
    )

    assert results[0].status == "downloaded"
    updated = json.loads(conversation_path(tmp_path, conversation).read_text())
    local_path = updated["chat_messages"][0]["files"][0]["local_path"]
    assert local_path.startswith("files/conv-1/")
    assert (tmp_path / local_path).read_bytes() == b"image-bytes"
    assert updated["chat_messages"][0]["files"][0]["preview_url"] == ref.url
    meta = json.loads(meta_path(tmp_path, conversation).read_text())
    assert meta["files"]["file-1"]["status"] == "downloaded"


def test_process_archived_files_dry_run_writes_nothing(tmp_path):
    conversation = "conv-1"
    ref = make_ref()
    payload = {
        "chat_messages": [
            {"files": [{"file_uuid": "file-1", "preview_url": ref.url}]}
        ]
    }
    _write_conversation(tmp_path, conversation, payload, {"media": {}})

    results = process_archived_files(
        tmp_path,
        [(conversation, payload)],
        lambda url: (_ for _ in ()).throw(AssertionError("fetch must not run")),
        dry_run=True,
    )

    assert results[0].status == "planned"
    assert not (tmp_path / "files").exists()
    assert json.loads(conversation_path(tmp_path, conversation).read_text()) == payload


def test_process_archived_files_records_unavailable(tmp_path):
    conversation = "conv-1"
    payload = {
        "chat_messages": [
            {
                "files": [
                    {
                        "file_uuid": "blob-1",
                        "file_name": "data.csv",
                        "file_kind": "blob",
                        "path": "/mnt/user-data/uploads/data.csv",
                    }
                ]
            }
        ]
    }
    _write_conversation(tmp_path, conversation, payload, {"media": {}})

    results = process_archived_files(
        tmp_path, [(conversation, payload)], lambda url: Fetched(b"", None)
    )

    assert results[0].status == "unavailable"
    meta = json.loads(meta_path(tmp_path, conversation).read_text())
    assert meta["file_failures"] == [
        {"file_uuid": "blob-1", "reason": "no downloadable URL in payload"}
    ]
