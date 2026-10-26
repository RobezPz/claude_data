import sys

import pytest

from claude_data import main, parse_args


def test_parse_view_defaults():
    ns = parse_args(["view"])
    assert ns.command == "view"
    assert ns.port is None and ns.archive is None and ns.data_dir is None
    assert ns.no_browser is False
    assert ns.show_browser is False


def test_parse_view_options():
    ns = parse_args(
        ["view", "--port", "9000", "--archive", "a", "--data-dir", "d", "--no-browser"]
    )
    assert (ns.port, str(ns.archive), str(ns.data_dir), ns.no_browser) == (
        9000,
        "a",
        "d",
        True,
    )


def test_view_rejects_archive_flags():
    with pytest.raises(SystemExit):
        parse_args(["view", "--refresh"])


def test_archive_rejects_view_flags():
    with pytest.raises(SystemExit):
        parse_args(["--port", "9000"])


def test_main_dispatches_view(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "claude_data.curator.server.serve", lambda **kw: calls.update(kw)
    )
    monkeypatch.setattr(
        sys, "argv", ["claude-data", "view", "--no-browser", "--port", "9000"]
    )
    main()
    assert calls["port"] == 9000 and calls["open_browser"] is False
