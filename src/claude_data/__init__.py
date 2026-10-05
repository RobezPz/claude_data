import argparse
import sys
from pathlib import Path

from claude_data.browser import is_logged_in, launch
from claude_data.collect import collect_conversations
from claude_data.env import MissingUsernameError, read_env, require_username
from claude_data.fetch import (
    CaptureError,
    capture_payload,
    fetch_all,
    process_conversation,
)
from claude_data.login import login
from claude_data.media import download_media
from claude_data.store import atomic_write_json, build_manifest

ARCHIVE_ROOT = Path("archive")
CONSECUTIVE_CAPTURE_FAILURES_ABORT = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="claude-data",
        description="Archive Claude.ai conversations for offline viewing.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="log in interactively before archiving",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="accepted for compatibility; the browser always runs headed",
    )
    parser.add_argument(
        "--refresh",
        nargs="?",
        const="all",
        default=None,
        metavar="UUID",
        help="re-archive everything, or just the given conversation uuid",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        username = require_username(read_env(Path(".env")))
    except MissingUsernameError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    context = launch(headless=False)
    page = context.new_page()
    logged_in = is_logged_in(page)
    if args.login or not logged_in:
        page = login(context, username)

    consecutive_failures = 0

    def capture(uuid: str) -> dict | None:
        nonlocal consecutive_failures
        payload = capture_payload(page, uuid)
        if payload is None:
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_CAPTURE_FAILURES_ABORT:
                print(
                    "captures keep failing — the session is probably logged out; "
                    "re-run with --login",
                    file=sys.stderr,
                )
                context.close()
                raise SystemExit(1) from None
        else:
            consecutive_failures = 0
        return payload

    refs = collect_conversations(page)
    refresh_uuids = (
        frozenset({args.refresh}) if args.refresh not in (None, "all") else frozenset()
    )
    force = args.refresh == "all"
    report = fetch_all(
        refs,
        root=ARCHIVE_ROOT,
        fetch_one=lambda uuid: process_conversation(
            ARCHIVE_ROOT,
            uuid,
            capture,
            lambda pairs: download_media(context, pairs, ARCHIVE_ROOT),
        ),
        force=force,
        refresh_uuids=refresh_uuids,
    )
    atomic_write_json(ARCHIVE_ROOT / "index.json", build_manifest(ARCHIVE_ROOT))
    context.close()
    print(
        f"fetched {len(report.fetched)}, skipped {report.skipped}, failed {len(report.failed)}"
    )
    if report.failed:
        print("failed uuids: " + ", ".join(report.failed), file=sys.stderr)
    raise SystemExit(1 if report.failed else 0)


__all__ = ["CaptureError", "main", "parse_args"]