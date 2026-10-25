from claude_data.curator.model import Message, Source
from claude_data.curator.pairs import (
    active_path,
    build_pairs,
    message_text,
    preview_text,
)


def msg(uuid, sender, index, parent=None, text="", created="2026-01-01T00:00:00Z"):
    return Message(
        external_id=uuid,
        parent_id=parent,
        role=sender,
        created_at=created,
        index=index,
        content=[{"type": "text", "text": text}],
        attachments=[],
        assets=[],
    )


def source(message_count=4):
    return Source(
        source_id="claude:conv-1",
        provider="claude",
        external_id="conv-1",
        title="T",
        model="m",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        raw_path="conversations/conv-1.json",
        message_count=message_count,
        assets=[],
    )


def test_active_path_walks_leaf_to_root():
    messages = [
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
        msg("h2", "human", 2, parent="a1"),
        msg("a2", "assistant", 3, parent="h2"),
    ]
    path, fallback = active_path(messages, "a2")
    assert [m.external_id for m in path] == ["h1", "a1", "h2", "a2"]
    assert fallback is False


def test_active_path_treats_root_sentinel_as_end():
    messages = [
        msg("h1", "human", 0, parent="00000000-0000-4000-8000-000000000000"),
        msg("a1", "assistant", 1, parent="h1"),
    ]
    path, fallback = active_path(messages, "a1")
    assert [m.external_id for m in path] == ["h1", "a1"]
    assert fallback is False


def test_active_path_falls_back_to_index_order():
    messages = [
        msg("h2", "human", 2),
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
    ]
    path, fallback = active_path(messages, "missing-leaf")
    assert [m.external_id for m in path] == ["h1", "a1", "h2"]
    assert fallback is True


def test_build_pairs_groups_answers_and_marks_missing():
    messages = [
        msg("h1", "human", 0, text="q1"),
        msg("a1", "assistant", 1, parent="h1", text="a1"),
        msg("a1b", "assistant", 2, parent="a1", text="a1b"),
        msg("h2", "human", 3, parent="a1b", text="q2"),
    ]
    build = build_pairs(source(4), messages, "h2")
    assert [p.pair_id for p in build.pairs] == ["claude:conv-1:h1", "claude:conv-1:h2"]
    assert build.pairs[0].answer_uuids == ["a1", "a1b"]
    assert build.pairs[0].question_text == "q1"
    assert build.pairs[1].missing_answer is True


def test_build_pairs_marks_branch():
    messages = [
        msg("h1", "human", 0),
        msg("a1", "assistant", 1, parent="h1"),
        msg("a2", "assistant", 2, parent="h1"),
        msg("h2", "human", 3, parent="a1"),
    ]
    build = build_pairs(source(4), messages, "h2")
    assert build.pairs[0].has_branches is True


def test_preview_text_joins_text_blocks_and_truncates():
    message = Message(
        external_id="h1",
        parent_id=None,
        role="human",
        created_at="2026-01-01T00:00:00Z",
        index=0,
        content=[{"type": "text", "text": "hello"}, {"type": "thinking", "thinking": "x"}],
        attachments=[],
        assets=[],
    )
    assert message_text(message) == "hello"
    assert preview_text(message, limit=5) == "hello"
    assert preview_text(message, limit=2) == "he"
