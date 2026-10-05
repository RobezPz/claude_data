import json
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page

from claude_data.browser import ResponseCapture

LIST_URL_PATTERN = r"chat_conversations_v2\?"

MAX_ROUNDS_WITHOUT_NEW_CONVERSATIONS = 3

INITIAL_LOAD_WAIT_MS = 8000
SCROLL_SETTLE_WAIT_MS = 2500

_SCROLL_CONTAINERS_TO_BOTTOM = """() => {
    let n = 0;
    for (const el of document.querySelectorAll('*')) {
        const s = getComputedStyle(el);
        if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100) {
            el.scrollTop = el.scrollHeight;
            n++;
        }
    }
    return n;
}"""


@dataclass(frozen=True)
class ConversationRef:
    uuid: str
    title: str
    created_at: str
    updated_at: str


def parse_list_response(obj: dict) -> list[ConversationRef]:
    refs: list[ConversationRef] = []
    for item in obj.get("data", []):
        if not isinstance(item, dict):
            continue
        refs.append(
            ConversationRef(
                uuid=str(item.get("uuid", "")),
                title=str(item.get("name", "")),
                created_at=str(item.get("created_at", "")),
                updated_at=str(item.get("updated_at", "")),
            )
        )
    return refs


def _known_uuids(capture: ResponseCapture) -> set[str]:
    return {
        ref.uuid
        for obj in capture.objects
        for ref in parse_list_response(obj)
    }


def _capture_all(page: Page) -> list[dict]:
    capture = ResponseCapture(LIST_URL_PATTERN)
    capture.attach(page)
    page.goto("https://claude.ai/chats", wait_until="domcontentloaded")
    page.wait_for_timeout(INITIAL_LOAD_WAIT_MS)
    stale_rounds = 0
    while stale_rounds < MAX_ROUNDS_WITHOUT_NEW_CONVERSATIONS:
        known = len(_known_uuids(capture))
        page.evaluate(_SCROLL_CONTAINERS_TO_BOTTOM)
        page.wait_for_timeout(SCROLL_SETTLE_WAIT_MS)
        if len(_known_uuids(capture)) > known:
            stale_rounds = 0
        else:
            stale_rounds += 1
    return capture.objects


def collect_conversations(page: Page) -> list[ConversationRef]:
    refs: list[ConversationRef] = []
    for obj in _capture_all(page):
        refs.extend(parse_list_response(obj))
    deduped: dict[str, ConversationRef] = {}
    for ref in refs:
        deduped.setdefault(ref.uuid, ref)
    return list(deduped.values())


def dump_raw(page: Page, path: Path) -> None:
    path.write_text(json.dumps(_capture_all(page), indent=2, ensure_ascii=False))
