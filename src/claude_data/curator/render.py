import html
import json
import re
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

_REMOTE_IMAGE = re.compile(
    r"""<img\b[^>]*\bsrc=["']https?://[^"']*["'][^>]*>""",
    re.IGNORECASE,
)


def _rewrite_local_links(text: str, link_prefix: str) -> str:
    for prefix in LOCAL_PREFIXES:
        text = text.replace(f"]({prefix}", f"]({link_prefix}{prefix}")
        text = text.replace(f'src="{prefix}', f'src="{link_prefix}{prefix}')
        text = text.replace(f"src='{prefix}", f"src='{link_prefix}{prefix}")
    return text


def render_markdown(text: str, link_prefix: str = "/assets/") -> str:
    text = _rewrite_local_links(text, link_prefix)
    rendered = markdown.markdown(text, extensions=["fenced_code", "tables"])
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    return _REMOTE_IMAGE.sub(
        '<span class="asset-note">[remote image]</span>', cleaned
    )


def _block_text(block: dict) -> str:
    for key in ("text", "thinking", "summary"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = block.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str) and item["text"].strip():
                parts.append(item["text"].strip())
            elif isinstance(item.get("summary"), str) and item["summary"].strip():
                parts.append(item["summary"].strip())
            elif item.get("title") or item.get("url"):
                parts.append(
                    " ".join(
                        str(value)
                        for value in (item.get("title"), item.get("url"))
                        if value
                    )
                )
        if parts:
            return " ".join(parts)
    summaries = block.get("summaries")
    if isinstance(summaries, list):
        parts = [
            str(item["summary"])
            for item in summaries
            if isinstance(item, dict)
            and isinstance(item.get("summary"), str)
            and item["summary"].strip()
        ]
        if parts:
            return " ".join(parts)
    return json.dumps(block, ensure_ascii=False)


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
    for attachment in message.attachments or []:
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("file_name") or "attachment")
        extracted = attachment.get("extracted_content")
        if isinstance(extracted, str) and extracted.strip():
            parts.append(_details(f"Attachment: {name}", extracted))
        else:
            parts.append(f'<p class="asset-note">Attachment: {html.escape(name)}</p>')
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
