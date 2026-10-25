import pytest
from curator_fixtures import message, write_conversation  # pyrefly: ignore[missing-import]

from claude_data.curator.library import Library
from claude_data.curator.model import Collection, CollectionItem


@pytest.fixture
def curator_archive(tmp_path):
    archive = tmp_path / "archive"
    (archive / "files" / "conv-1").mkdir(parents=True)
    (archive / "files" / "conv-1" / "photo.webp").write_bytes(b"image")
    payload = {
        "uuid": "conv-1",
        "name": "T",
        "model": "m",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "current_leaf_message_uuid": "a1",
        "chat_messages": [
            {
                **message("h1", "human", 0, text="q"),
                "files": [
                    {
                        "file_uuid": "f1",
                        "file_name": "photo.png",
                        "file_kind": "image",
                        "local_path": "files/conv-1/photo.webp",
                    }
                ],
            },
            message("a1", "assistant", 1, parent="h1", text="a"),
        ],
    }
    write_conversation(archive, payload)
    return archive


@pytest.fixture
def curator_library(curator_archive):
    return Library.build(curator_archive)


@pytest.fixture
def curator_collection(curator_library):
    pair = curator_library.pairs[0]
    return Collection(
        collection_id="c1",
        title="Topic",
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
