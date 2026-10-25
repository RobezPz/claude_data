import json

import pytest

from claude_data.curator.model import Collection, CollectionItem
from claude_data.curator.store import (
    RevisionConflict,
    StoreError,
    load_state,
    save_state,
    validate_collections,
)


def coll(cid="c1", title="Topic"):
    return Collection(
        collection_id=cid,
        title=title,
        description="",
        items=[
            CollectionItem(
                pair_id="p1",
                source_id="claude:conv-1",
                question_uuid="h1",
                date="2026-01-01T00:00:00Z",
                preview="q",
            )
        ],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_load_missing_state_returns_empty(tmp_path):
    state = load_state(tmp_path)
    assert state.revision == 0 and state.collections == []


def test_save_increments_revision_and_writes_backup(tmp_path):
    first = save_state(tmp_path, 0, [coll()])
    assert first.revision == 1
    second = save_state(tmp_path, 1, [coll(title="Changed")])
    assert second.revision == 2
    assert json.loads((tmp_path / "collections.json.bak").read_text())["revision"] == 1


def test_save_state_rejects_stale_revision(tmp_path):
    save_state(tmp_path, 0, [coll()])
    with pytest.raises(RevisionConflict):
        save_state(tmp_path, 0, [coll(title="Stale")])


def test_validate_rejects_duplicate_ids_and_empty_title():
    with pytest.raises(ValueError):
        validate_collections([coll(), coll()])
    with pytest.raises(ValueError):
        validate_collections([coll(title="")])


def test_validate_allows_unresolved_pair():
    validate_collections([coll()])


def test_malformed_state_raises_store_error(tmp_path):
    (tmp_path / "collections.json").write_text("{not json")
    with pytest.raises(StoreError):
        load_state(tmp_path)
