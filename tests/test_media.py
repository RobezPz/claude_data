import re

from claude_data.archiver.media import (
    derive_media_filename,
    plan_downloads,
    rewrite_urls,
)


def test_rewrite_urls_replaces_exact_and_embedded():
    payload = {"a": "https://cdn.example.com/f/1.png",
               "text": "![img](https://cdn.example.com/f/1.png)",
               "nested": [{"b": "https://cdn.example.com/f/2.png"}],
               "untouched": "plain text"}
    out = rewrite_urls(payload, {"https://cdn.example.com/f/1.png": "media/1.png"})
    assert out["a"] == "media/1.png"
    assert out["text"] == "![img](media/1.png)"
    assert out["nested"][0]["b"] == "https://cdn.example.com/f/2.png"
    assert out["untouched"] == "plain text"
    assert payload["a"] == "https://cdn.example.com/f/1.png"  # original not mutated


def test_derive_media_filename_from_url_path():
    assert derive_media_filename("https://cdn.example.com/a/photo.png") == "photo.png"


def test_derive_media_filename_collision():
    url = "https://cdn.example.com/b/photo.png"
    name = derive_media_filename(url, taken={"photo.png"})
    stem, _, suffix = name.rpartition(".")
    assert name != "photo.png" and stem.startswith("photo-") and suffix == "png"
    assert derive_media_filename(url, taken={"photo.png", name}) != name  # never re-collides


def test_derive_media_filename_no_path_uses_hash():
    name = derive_media_filename("https://cdn.example.com/?q=1")
    assert re.fullmatch(r"file-[0-9a-f]{12}", name)


def test_plan_downloads_skips_existing_and_dedupes(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "have.png").write_bytes(b"x")
    plan = plan_downloads(
        ["https://cdn.example.com/have.png", "https://cdn.example.com/have.png",
         "https://cdn.example.com/new.png"], tmp_path)
    assert plan.present == {"https://cdn.example.com/have.png": "media/have.png"}
    assert plan.to_download == [("https://cdn.example.com/new.png", "new.png")]
