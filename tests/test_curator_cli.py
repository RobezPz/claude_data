import sys

import pytest

from claude_data import main, parse_args


def test_parse_view_defaults():
    ns = parse_args(["view"])
    assert ns.command == "view"
    assert ns.port is None and ns.archive is None and ns.data_dir is None
    assert ns.open is False


def test_parse_view_options():
    ns = parse_args(
        ["view", "--port", "9000", "--archive", "a", "--data-dir", "d", "--open"]
    )
    assert (ns.port, str(ns.archive), str(ns.data_dir), ns.open) == (
        9000,
        "a",
        "d",
        True,
    )


def test_view_rejects_sync_flags():
    with pytest.raises(SystemExit):
        parse_args(["view", "--refresh"])


def test_sync_rejects_view_flags():
    with pytest.raises(SystemExit):
        parse_args(["sync", "--port", "9000"])


def test_main_dispatches_view_without_opening_a_browser(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "claude_data.curator.server.serve", lambda **kw: calls.update(kw)
    )
    monkeypatch.setattr(sys, "argv", ["claude-data", "view", "--port", "9000"])
    main()
    assert calls["port"] == 9000 and calls["open_browser"] is False


def test_main_dispatches_view_with_open(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "claude_data.curator.server.serve", lambda **kw: calls.update(kw)
    )
    monkeypatch.setattr(sys, "argv", ["claude-data", "view", "--open"])
    main()
    assert calls["open_browser"] is True
