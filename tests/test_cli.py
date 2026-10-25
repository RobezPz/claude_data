import pytest

from claude_data import parse_args


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.login is False and ns.headed is False and ns.refresh is None
    assert ns.download_files is False
    assert ns.export_artifacts is False
    assert ns.conversation is None
    assert ns.max_size_mb == 0.0
    assert ns.force is False
    assert ns.dry_run is False
    assert ns.show_browser is False
    assert ns.command is None


def test_parse_args_show_browser():
    assert parse_args(["--show-browser"]).show_browser is True


def test_parse_args_login_headed():
    ns = parse_args(["--login", "--headed"])
    assert ns.login and ns.headed and ns.refresh is None


def test_parse_args_refresh_all_and_single():
    assert parse_args(["--refresh"]).refresh == "all"
    assert (
        parse_args(["--refresh", "2098eb49-0fcc-4fb9-8050-57e652b6482d"]).refresh
        == "2098eb49-0fcc-4fb9-8050-57e652b6482d"
    )


def test_parse_args_download_files_options():
    ns = parse_args(
        [
            "--download-files",
            "--max-size-mb",
            "25",
            "--conversation",
            "conv-1",
            "--force",
            "--dry-run",
        ]
    )
    assert ns.download_files is True
    assert ns.max_size_mb == 25.0
    assert ns.conversation == "conv-1"
    assert ns.force is True
    assert ns.dry_run is True


def test_parse_args_export_artifacts():
    ns = parse_args(["--export-artifacts", "--conversation", "conv-1"])
    assert ns.export_artifacts is True
    assert ns.conversation == "conv-1"


def test_parse_args_rejects_refresh_with_file_mode():
    with pytest.raises(SystemExit):
        parse_args(["--download-files", "--refresh"])


def test_parse_args_conversation_requires_file_mode():
    with pytest.raises(SystemExit):
        parse_args(["--conversation", "conv-1"])


def test_parse_args_rejects_negative_max_size():
    with pytest.raises(SystemExit):
        parse_args(["--download-files", "--max-size-mb", "-1"])
