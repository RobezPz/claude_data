from claude_data.browser import minimize, restore


class FakeSession:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.detached = False

    def send(self, method, params=None):
        self.calls.append((method, params))
        if self.fail:
            raise RuntimeError("cdp unavailable")
        return {"windowId": 7}

    def detach(self):
        self.detached = True


class FakeContext:
    def __init__(self, fail=False):
        self.session = FakeSession(fail=fail)

    def new_cdp_session(self, page):
        return self.session


def test_minimize_sets_the_window_state():
    context = FakeContext()

    assert minimize(context, object()) is True
    assert context.session.calls == [
        ("Browser.getWindowForTarget", None),
        (
            "Browser.setWindowBounds",
            {"windowId": 7, "bounds": {"windowState": "minimized"}},
        ),
    ]
    assert context.session.detached is True


def test_restore_sets_the_window_state():
    context = FakeContext()

    assert restore(context, object()) is True
    assert context.session.calls[-1] == (
        "Browser.setWindowBounds",
        {"windowId": 7, "bounds": {"windowState": "normal"}},
    )


def test_window_state_failure_is_non_fatal():
    context = FakeContext(fail=True)

    assert minimize(context, object()) is False
    assert context.session.detached is True
