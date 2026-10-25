import zipfile

from claude_data.curator.export import copy_assets, export_collection, rewrite_links
from claude_data.curator.model import Asset, Collection, CollectionItem


def test_export_writes_bundle_and_copies_assets(
    tmp_path, curator_archive, curator_library, curator_collection
):
    result = export_collection(
        curator_collection, curator_library, curator_archive, tmp_path / "exports"
    )
    assert (result.folder / "collection.md").is_file()
    assert (result.folder / "collection.html").is_file()
    assert (result.folder / "collection.json").is_file()
    assert list((result.folder / "assets").iterdir())
    assert "2026-01-01" in (result.folder / "collection.md").read_text()
    assert "assets/" in (result.folder / "collection.md").read_text()


def test_export_zip_contains_bundle(
    tmp_path, curator_archive, curator_library, curator_collection
):
    result = export_collection(
        curator_collection, curator_library, curator_archive, tmp_path / "exports"
    )
    with zipfile.ZipFile(result.zip_path) as archive:
        names = archive.namelist()
    assert any(name.endswith("collection.md") for name in names)
    assert any("/assets/" in name for name in names)


def test_export_missing_pair_placeholder(tmp_path, curator_archive, curator_library):
    missing = Collection(
        collection_id="c2",
        title="Missing",
        description="",
        items=[
            CollectionItem(
                pair_id="gone",
                source_id="claude:gone",
                question_uuid="gone",
                date="2026-01-01T00:00:00Z",
                preview="lost question",
            )
        ],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    result = export_collection(
        missing, curator_library, curator_archive, tmp_path / "exports"
    )
    assert "missing" in (result.folder / "collection.md").read_text().lower()


def test_copy_assets_dedupes_by_hash(tmp_path, curator_archive):
    first = Asset("a1", "image", "one", local_path="files/conv-1/photo.webp")
    second = Asset("a2", "image", "two", local_path="files/conv-1/photo.webp")
    mapping, missing = copy_assets([first, second], curator_archive, tmp_path / "assets")
    assert len(set(mapping.values())) == 1
    assert missing == []


def test_rewrite_links_uses_export_paths():
    assert (
        rewrite_links("![x](media/a.png)", {"media/a.png": "assets/abc-a.png"})
        == "![x](assets/abc-a.png)"
    )
