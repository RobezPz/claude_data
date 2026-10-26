from claude_data.archiver.fetch import read_org


class FakePage:
    def __init__(self, value):
        self.value = value
        self.script = None
        self.argument = None

    def evaluate(self, script, argument=None):
        self.script = script
        self.argument = argument
        return self.value


def test_read_org_returns_last_active_org_cookie():
    page = FakePage("org-1")

    assert read_org(page) == "org-1"
    assert page.argument == "lastActiveOrg"


def test_read_org_returns_none_when_cookie_missing():
    assert read_org(FakePage(None)) is None
