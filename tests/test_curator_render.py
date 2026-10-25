from claude_data.curator.model import Asset, Message, Pair
from claude_data.curator.render import render_markdown, render_message, render_pair


def msg(uuid, role, content):
    return Message(
        external_id=uuid,
        parent_id=None,
        role=role,
        created_at="2026-01-01T00:00:00Z",
        index=0,
        content=content,
        attachments=[],
        assets=[],
    )


def test_render_markdown_strips_scripts():
    html = render_markdown("<script>alert(1)</script> **bold**")
    assert "<script>" not in html
    assert "<strong>bold</strong>" in html


def test_render_markdown_rewrites_local_assets():
    html = render_markdown("![x](media/a.png)")
    assert 'src="/assets/media/a.png"' in html


def test_render_message_escapes_tool_input():
    html = render_message(
        msg(
            "a1",
            "assistant",
            [
                {
                    "type": "tool_use",
                    "name": "bash",
                    "input": {"command": "<script>x</script>"},
                }
            ],
        )
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_pair_returns_assets():
    asset = Asset(asset_id="a1", kind="image", label="x", local_path="files/x.webp")
    question = msg("h1", "human", [{"type": "text", "text": "q"}])
    answer = msg("a1", "assistant", [{"type": "text", "text": "a"}])
    pair = Pair(
        "p1",
        "claude:conv-1",
        "h1",
        ["a1"],
        "2026-01-01T00:00:00Z",
        "q",
        "a",
        False,
        False,
        [asset],
    )
    rendered = render_pair(pair, question, [answer])
    assert rendered["assets"][0]["kind"] == "image"
    assert "q" in rendered["question_html"]
