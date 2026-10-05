import time

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from claude_data.browser import is_logged_in

EMAIL_INPUT = 'input[type="email"]'
CONTINUE_BUTTON = 'button[type="submit"]'

EMAIL_INPUT_TIMEOUT_MS = 30_000
POLL_INTERVAL_MS = 2_000


class LoginTimeout(RuntimeError):
    pass


def login(context: BrowserContext, email: str, timeout_s: float = 600.0) -> Page:
    page = context.new_page()
    page.goto("https://claude.ai/login", wait_until="domcontentloaded")
    try:
        page.wait_for_selector(EMAIL_INPUT, timeout=EMAIL_INPUT_TIMEOUT_MS)
    except PlaywrightTimeout:
        raise LoginTimeout("email input not found on the login page") from None
    page.fill(EMAIL_INPUT, email)
    page.click(CONTINUE_BUTTON)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if "/login" not in page.url and "claude.ai" in page.url:
            return page
        page.wait_for_timeout(POLL_INTERVAL_MS)
    if is_logged_in(page):
        return page
    raise LoginTimeout(f"login not completed within {timeout_s} seconds")
