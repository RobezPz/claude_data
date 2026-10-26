from pathlib import Path


class MissingUsernameError(RuntimeError):
    pass


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key.strip()] = value
    return env


def require_username(env: dict[str, str]) -> str:
    username = env.get("CLAUDE_USERNAME")
    if not username:
        raise MissingUsernameError("CLAUDE_USERNAME missing from .env")
    return username
