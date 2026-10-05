from playwright.sync_api import Page

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


def capture_payload(page: Page, uuid: str) -> dict | None:
    if not page.url.startswith("https://claude.ai"):
        page.goto(CHATS_URL, wait_until="domcontentloaded")
    org = page.evaluate(_READ_ORG_JS, _ORG_COOKIE)
    if not org:
        return None
    result = page.evaluate(_FETCH_TREE_JS, {"org": org, "uuid": uuid})
    if not isinstance(result, dict) or result.get("status") != 200:
        return None
    body = result.get("body")
    return body if isinstance(body, dict) else None