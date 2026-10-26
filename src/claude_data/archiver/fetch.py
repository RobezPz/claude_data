from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page

from claude_data.archiver.collect import ConversationRef
from claude_data.archiver.media import extract_media_urls, plan_downloads, rewrite_urls
from claude_data.archiver.store import (
    atomic_write_json,
    conversation_path,
    list_archived,
    meta_path,
)

CHATS_URL = "https://claude.ai/chats"
CONVERSATION_URL = "https://claude.ai/chat/{uuid}"

_ORG_COOKIE = "lastActiveOrg"

_READ_ORG_JS = """(name) => {
    for (const part of document.cookie.split(';')) {
        const trimmed = part.trim();
        const eq = trimmed.indexOf('=');
        if (trimmed.slice(0, eq) === name) {
            return decodeURIComponent(trimmed.slice(eq + 1));
        }
    }
    return null;
}"""

_FETCH_TREE_JS = """async ({org, uuid}) => {
    const params = new URLSearchParams();
    params.append('tree', 'True');
    params.append('rendering_mode', 'messages');
    params.append('render_all_tools', 'true');
    params.append('include_inline_comparison', 'true');
    params.append('consistency', 'strong');
    const url = `/api/organizations/${org}/chat_conversations/${uuid}?${params.toString()}`;
    const res = await fetch(url, {
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
            'anthropic-client-platform': 'web_claude_ai',
        },
    });
    let body = null;
    try {
        body = await res.json();
    } catch (err) {
        body = null;
    }
    return {status: res.status, body: body};
}"""


def read_org(page: Page) -> str | None:
    value = page.evaluate(_READ_ORG_JS, _ORG_COOKIE)
    return value if isinstance(value, str) else None


def capture_payload(page: Page, uuid: str) -> dict | None:
    if not page.url.startswith("https://claude.ai"):
        page.goto(CHATS_URL, wait_until="domcontentloaded")
    org = read_org(page)
    if not org:
        return None
    result = page.evaluate(_FETCH_TREE_JS, {"org": org, "uuid": uuid})
    if not isinstance(result, dict) or result.get("status") != 200:
        return None
    body = result.get("body")
    return body if isinstance(body, dict) else None


class CaptureError(RuntimeError):
    pass


def process_conversation(
    root: Path,
    uuid: str,
    capture: Callable[[str], dict | None],
    download: Callable[[list[tuple[str, str]]], tuple[dict[str, str], list[str]]],
) -> None:
    payload = capture(uuid)
    if payload is None:
        raise CaptureError(f"no payload captured for {uuid}")
    plan = plan_downloads(extract_media_urls(payload), root)
    successes, failures = download(plan.to_download) if plan.to_download else ({}, [])
    mapping = dict(plan.present)
    mapping.update(successes)
    atomic_write_json(conversation_path(root, uuid), rewrite_urls(payload, mapping))
    atomic_write_json(
        meta_path(root, uuid),
        {"media": mapping, "media_failures": sorted(failures)},
    )


@dataclass(frozen=True)
class FetchReport:
    fetched: list[str]
    failed: list[str]
    skipped: int


CAPTURE_ATTEMPTS = 3


def fetch_all(
    refs: list[ConversationRef],
    root: Path,
    fetch_one: Callable[[str], None],
    force: bool = False,
    refresh_uuids: frozenset[str] = frozenset(),
) -> FetchReport:
    archived = set() if force else list_archived(root)
    fetched: list[str] = []
    failed: list[str] = []
    skipped = 0
    for ref in refs:
        if ref.uuid in archived and ref.uuid not in refresh_uuids:
            skipped += 1
            continue
        for attempt in range(CAPTURE_ATTEMPTS):
            try:
                fetch_one(ref.uuid)
            except CaptureError:
                continue
            fetched.append(ref.uuid)
            break
        else:
            failed.append(ref.uuid)
    return FetchReport(fetched=fetched, failed=failed, skipped=skipped)

