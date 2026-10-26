import pytest

from claude_data.archiver.env import MissingUsernameError, read_env, require_username


def test_read_env_parses_values_quotes_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nCLAUDE_USERNAME='user@example.com'\nEMPTY=\n")
    env = read_env(env_file)
    assert env == {"CLAUDE_USERNAME": "user@example.com", "EMPTY": ""}


def test_require_username_missing_raises_without_leaking_values():
    with pytest.raises(MissingUsernameError, match="CLAUDE_USERNAME"):
        require_username({"OTHER": "secret-value"})


def test_require_username_present_returns_value():
    assert (
        require_username({"CLAUDE_USERNAME": "user@example.com"}) == "user@example.com"
    )
