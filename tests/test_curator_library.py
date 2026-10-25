import json

from curator_fixtures import message, write_conversation  # pyrefly: ignore[missing-import]

from claude_data.curator.library import Library
from claude_data.curator.model import Collection, CollectionItem


def item(pair_id, source_id, question_uuid, date, preview):
    return CollectionItem(
        pair_id=pair_id,
        source_id=source_id,
        question_uuid=question_uuid,
        date=date,
        preview=preview,
    )


def make_collection(*items):
    return Collection(
        collection_id="c1",
        title="Topic",
        description="",
        items=list(items),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def make_library(tmp_path):
    for conv, uuid, date in [
        ("conv-1", "h1", "2026-01-02T00:00:00Z"),
        ("conv-2", "h2", "2026-01-01T00:00:00Z"),
    ]:
        payload = {
            "uuid": conv,
            "name": conv,
            "model": "m",
            "created_at": date,
            "updated_at": date,
            "current_leaf_message_uuid": f"{uuid}-a",
            "chat_messages": [
                message(uuid, "human", 0, text="q", created=date),
                message(f"{uuid}-a", "assistant", 1, parent=uuid, text="a", created=date),
            ],
        }
        write_conversation(tmp_path, payload)
    return Library.build(tmp_path)


def test_build_orders_pairs_by_date(tmp_path):
    library = make_library(tmp_path)
    assert [p.source_id for p in library.pairs] == ["claude:conv-2", "claude:conv-1"]


def test_pair_content_returns_question_and_answers(tmp_path):
    library = make_library(tmp_path)
    pair = library.pairs[0]
    content = library.pair_content(pair.pair_id)
    assert content["pair"]["pair_id"] == pair.pair_id
    assert content["question"]["external_id"] == pair.question_uuid
    assert content["answers"][0]["role"] == "assistant"


def test_to_json_is_serializable(tmp_path):
    library = make_library(tmp_path)
    payload = library.to_json()
    json.dumps(payload)
    assert {"sources", "pairs"} <= set(payload)


def test_reconcile_relinks_by_question_uuid(tmp_path):
    library = make_library(tmp_path)
    old = library.pairs[0]
    collection = make_collection(
        item(
            pair_id="missing",
            source_id=old.source_id,
            question_uuid=old.question_uuid,
            date=old.date,
            preview=old.question_text,
        )
    )
    reconciled = library.reconcile([collection])
    assert reconciled[0].items[0].pair_id == old.pair_id


def test_reconcile_keeps_missing_item(tmp_path):
    library = make_library(tmp_path)
    collection = make_collection(
        item(
            pair_id="gone",
            source_id="claude:gone",
            question_uuid="gone",
            date="2026-01-01T00:00:00Z",
            preview="gone",
        )
    )
    reconciled = library.reconcile([collection])
    assert reconciled[0].items[0].pair_id == "gone"
