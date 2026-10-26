import pytest

from claude_data import parse_args


def test_parse_sync_defaults():
    ns = parse_args(["sync"])
    assert ns.command == "sync"
    assert ns.login is False and ns.refresh is None
    assert ns.conversation is None
    assert ns.max_size_mb == 0.0
    assert ns.force is False
    assert ns.dry_run is False
    assert ns.show_browser is False


def test_parse_sync_login_and_show_browser():
    ns = parse_args(["sync", "--login", "--show-browser"])
    assert ns.login is True
    assert ns.show_browser is True


def test_parse_sync_refresh_all_and_single():
    assert parse_args(["sync", "--refresh"]).refresh == "all"
    assert (
        parse_args(["sync", "--refresh", "2098eb49-0fcc-4fb9-8050-57e652b6482d"]).refresh
        == "2098eb49-0fcc-4fb9-8050-57e652b6482d"
    )


def test_parse_sync_file_options():
    ns = parse_args(
        [
            "sync",
            "--conversation",
            "conv-1",
            "--max-size-mb",
            "25",
            "--force",
        ]
    )
    assert ns.conversation == "conv-1"
    assert ns.max_size_mb == 25.0
    assert ns.force is True


def test_parse_sync_dry_run():
    ns = parse_args(["sync", "--dry-run"])
    assert ns.dry_run is True


@pytest.mark.parametrize("argv", [[], ["--force"], ["--dry-run"]])
def test_bare_invocation_names_both_commands(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse_args(argv)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "sync" in err and "view" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["sync", "--port", "9000"],
        ["sync", "--archive", "a"],
        ["sync", "--data-dir", "d"],
        ["sync", "--open"],
    ],
)
def test_sync_rejects_view_flags(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["view", "--login"],
        ["view", "--refresh"],
        ["view", "--show-browser"],
        ["view", "--conversation", "conv-1"],
        ["view", "--max-size-mb", "25"],
        ["view", "--force"],
        ["view", "--dry-run"],
    ],
)
def test_view_rejects_sync_flags(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


@pytest.mark.parametrize("flag", ["--download-files", "--export-artifacts", "--headed", "--no-browser"])
def test_removed_flags_are_rejected(flag):
    with pytest.raises(SystemExit):
        parse_args(["sync", flag])


def test_parse_rejects_negative_max_size():
    with pytest.raises(SystemExit):
        parse_args(["sync", "--max-size-mb", "-1"])


def test_parse_rejects_login_or_refresh_with_dry_run():
    with pytest.raises(SystemExit):
        parse_args(["sync", "--dry-run", "--login"])
    with pytest.raises(SystemExit):
        parse_args(["sync", "--dry-run", "--refresh"])
