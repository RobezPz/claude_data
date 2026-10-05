from claude_data import parse_args


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.login is False and ns.headed is False and ns.refresh is None


def test_parse_args_login_headed():
    ns = parse_args(["--login", "--headed"])
    assert ns.login and ns.headed and ns.refresh is None


def test_parse_args_refresh_all_and_single():
    assert parse_args(["--refresh"]).refresh == "all"
    assert (
        parse_args(["--refresh", "2098eb49-0fcc-4fb9-8050-57e652b6482d"]).refresh
        == "2098eb49-0fcc-4fb9-8050-57e652b6482d"
    )