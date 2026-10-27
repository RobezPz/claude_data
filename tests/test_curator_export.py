import zipfile

import mdformat
import pytest
from curator_fixtures import message, write_conversation  # pyrefly: ignore[missing-import]

from claude_data.curator.export import copy_assets, export_collection, rewrite_links
from claude_data.curator.library import Library
from claude_data.curator.model import Asset, Collection, CollectionItem

WRAP = {"wrap": 80}
FRONTMATTER = {"frontmatter"}


def _export_fixture(
    tmp_path,
    question_text="",
    answer_text="",
    *,
    question_blocks=None,
    asset=False,
    title="Topic",
):
    archive = tmp_path / "archive"
    if asset:
        (archive / "files" / "conv-1").mkdir(parents=True)
        (archive / "files" / "conv-1" / "photo.webp").write_bytes(b"image")
    question = message("h1", "human", 0, text="")
    question["content"] = question_blocks or [{"type": "text", "text": question_text}]
    if asset:
        question["files"] = [
            {
                "file_uuid": "f1",
                "file_name": "screen shot.png",
                "file_kind": "image",
                "local_path": "files/conv-1/photo.webp",
            }
        ]
    payload = {
        "uuid": "conv-1",
        "name": "T",
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            question,
            message("a1", "assistant", 1, parent="h1", text=answer_text),
        ],
    }
    write_conversation(archive, payload)
    library = Library.build(archive)
    pair = library.pairs[0]
    collection = Collection(
        collection_id="c1",
        title=title,
        description="",
        items=[
            CollectionItem(
                pair_id=pair.pair_id,
                source_id=pair.source_id,
                question_uuid=pair.question_uuid,
                date=pair.date,
                preview=pair.question_text,
            )
        ],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    result = export_collection(collection, library, archive, tmp_path / "exports")
    return (
        (result.folder / "collection.md").read_text(),
        (result.folder / "collection.html").read_text(),
    )


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


def test_export_encodes_spaces_in_markdown_asset_links(
    tmp_path, curator_archive, curator_library, curator_collection
):
    result = export_collection(
        curator_collection, curator_library, curator_archive, tmp_path / "exports"
    )
    markdown = (result.folder / "collection.md").read_text()
    assert "%20" in markdown


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


def test_export_markdown_blockquotes_the_question(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path, "Why is the sky blue?", "Because of Rayleigh scattering."
    )

    assert "> **Q:**" in markdown
    assert "> Why is the sky blue?" in markdown
    answer_index = markdown.index("Because of Rayleigh scattering.")
    assert markdown[answer_index - 1] == "\n"
    assert not markdown[:answer_index].rstrip().endswith(">")


def test_export_markdown_keeps_separate_text_blocks_as_paragraphs(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path,
        question_blocks=[
            {"type": "text", "text": "First paragraph."},
            {"type": "text", "text": "Second paragraph."},
        ],
        answer_text="Answer.",
    )

    assert "> First paragraph.\n>\n> Second paragraph." in markdown


def test_export_markdown_labels_attachments_without_emphasis(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path, "Which file?", "This one.", asset=True
    )

    assert "**Attachments**" not in markdown
    assert "Attachments:" in markdown


def test_export_markdown_is_canonically_formatted(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path,
        "Intro paragraph.\n- item one\n- item two",
        "Answer.\n```python\nprint(1)\n```",
    )

    assert mdformat.text(markdown, options=WRAP, extensions=FRONTMATTER) == markdown
    assert markdown.startswith("---\n")
    assert "\n\n\n" not in markdown


def test_export_markdown_wraps_long_prose_at_80_columns(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path, "word " * 40, "answer " * 40
    )

    long_lines = [line for line in markdown.splitlines() if len(line) > 80]
    assert long_lines == []


def test_export_html_styles_the_question_as_a_callout(tmp_path):
    _, html = _export_fixture(tmp_path, "Why?", "Because.")

    assert '<section class="question">' in html
    rule = html.split(".question {")[1].split("}")[0]
    assert "border-left" in rule
    assert "background" in rule


def test_export_front_matter_quotes_titles_with_colons(tmp_path):
    markdown, _ = _export_fixture(
        tmp_path, "Question?", "Answer.", title="Walmart: scraping orders"
    )

    assert 'title: "Walmart: scraping orders"' in markdown


def test_export_refuses_unsafe_collection_id(tmp_path):
    collection = Collection(
        collection_id="..",
        title="Unsafe",
        description="",
        items=[],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError):
        export_collection(collection, None, tmp_path, tmp_path / "exports")
