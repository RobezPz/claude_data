import contextlib
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Response, sync_playwright

PROFILE_DIR = Path.home() / ".cache" / "claude_data" / "profile"

_playwright: Any = None


def launch(headless: bool = True) -> BrowserContext:
    global _playwright
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if _playwright is None:
        _playwright = sync_playwright().start()
    return _playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless
    )


def is_logged_in(page: Page) -> bool:
    page.goto("https://claude.ai/chats", wait_until="domcontentloaded")
    return "/login" not in page.url


class ResponseCapture:
    def __init__(self, pattern: str) -> None:
        self._pattern = re.compile(pattern)
        self.objects: list[dict] = []

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        if not self._pattern.search(response.url):
            return
        with contextlib.suppress(Exception):
            self.objects.append(response.json())
