import html
import json
from dataclasses import asdict

import bleach
import markdown

from claude_data.curator.model import Message, Pair

LOCAL_PREFIXES = ("media/", "files/", "artifacts/", "generated/")
ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "del",
    "details",
    "summary",
    "span",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "details": ["open"],
    "span": ["class"],
}


def _rewrite_local_links(text: str, link_prefix: str) -> str:
    for prefix in LOCAL_PREFIXES:
        text = text.replace(f"]({prefix}", f"]({link_prefix}{prefix}")
        text = text.replace(f'src="{prefix}', f'src="{link_prefix}{prefix}')
        text = text.replace(f"src='{prefix}", f"src='{link_prefix}{prefix}")
    return text


def render_markdown(text: str, link_prefix: str = "/assets/") -> str:
    text = _rewrite_local_links(text, link_prefix)
    rendered = markdown.markdown(text, extensions=["fenced_code", "tables"])
    return bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def _block_text(block: dict) -> str:
    if isinstance(block.get("text"), str):
        return block["text"]
    if isinstance(block.get("thinking"), str):
        return block["thinking"]
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def _details(summary: str, text: str) -> str:
    return (
        f"<details><summary>{html.escape(summary)}</summary>"
        f"<pre>{html.escape(text)}</pre></details>"
    )


def render_message(message: Message, link_prefix: str = "/assets/") -> str:
    parts: list[str] = []
    for block in message.content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(render_markdown(str(block.get("text") or ""), link_prefix))
        elif kind == "thinking":
            parts.append(_details("Thinking", _block_text(block)))
        elif kind == "tool_use":
            name = str(block.get("name") or "tool")
            payload = json.dumps(
                block.get("input") or {}, indent=2, ensure_ascii=False
            )
            parts.append(_details(f"Tool: {name}", payload))
        elif kind == "tool_result":
            parts.append(_details("Tool result", _block_text(block)))
        elif kind == "image":
            parts.append('<span class="asset-note">[image]</span>')
        else:
            parts.append(_details(str(kind or "block"), json.dumps(block, ensure_ascii=False)))
    return "".join(parts)


def render_pair(
    pair: Pair,
    question: Message,
    answers: list[Message],
    link_prefix: str = "/assets/",
) -> dict:
    return {
        "question_html": render_message(question, link_prefix),
        "answer_html": "".join(render_message(answer, link_prefix) for answer in answers),
        "assets": [asdict(asset) for asset in pair.assets],
    }
