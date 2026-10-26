import argparse
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_data.archiver.artifacts import process_archived_artifacts
from claude_data.archiver.browser import is_logged_in, launch, minimize, restore
from claude_data.archiver.collect import collect_conversations
from claude_data.archiver.env import MissingUsernameError, read_env, require_username
from claude_data.archiver.fetch import (
    CaptureError,
    capture_payload,
    fetch_all,
    process_conversation,
    read_org,
)
from claude_data.archiver.files import (
    DownloadError,
    Fetched,
    process_archived_files,
)
from claude_data.archiver.generated import generated_url, process_archived_generated
from claude_data.archiver.login import login
from claude_data.archiver.media import download_media
from claude_data.archiver.store import (
    atomic_write_json,
    build_manifest,
    load_archived,
)

ARCHIVE_ROOT = Path("archive")
CONSECUTIVE_CAPTURE_FAILURES_ABORT = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="claude-data",
        description=(
            "Archive Claude.ai conversations and their files ('sync') or browse "
            "them locally ('view')."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["sync", "view"],
        default=None,
        help="'sync' archives conversations and files; 'view' starts the curator",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="log in interactively before syncing",
    )
    parser.add_argument(
        "--refresh",
        nargs="?",
        const="all",
        default=None,
        metavar="UUID",
        help="re-fetch everything, or just the given conversation uuid",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="keep the browser visible instead of minimizing it",
    )
    parser.add_argument(
        "--conversation",
        metavar="UUID",
        default=None,
        help="limit the file and artifact passes to one conversation",
    )
    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=0.0,
        metavar="MB",
        help="skip files larger than this; 0 means no limit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite files that already exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned downloads without launching a browser or writing files",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="view server port (default 8765)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="archive root for view (default archive/)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="curated data directory for view (default curated/)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the curator in a browser",
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("choose a command: sync or view")
    if args.command == "view":
        sync_flags = (
            args.login
            or args.refresh is not None
            or args.show_browser
            or args.conversation is not None
            or args.max_size_mb != 0.0
            or args.force
            or args.dry_run
        )
        if sync_flags:
            parser.error("sync flags cannot be used with view")
        return args
    if (
        args.port is not None
        or args.archive is not None
        or args.data_dir is not None
        or args.open
    ):
        parser.error("--port/--archive/--data-dir/--open require view")
    if args.max_size_mb < 0:
        parser.error("--max-size-mb must be zero or positive")
    if args.dry_run and args.login:
        parser.error("--dry-run cannot be combined with --login")
    if args.dry_run and args.refresh is not None:
        parser.error("--dry-run cannot be combined with --refresh")
    return args


def _open_context(force_login: bool, show_browser: bool = False):
    username = require_username(read_env(Path(".env")))
    context = launch(headless=False)
    try:
        page = context.new_page()
        if not show_browser:
            minimize(context, page)
        if force_login or not is_logged_in(page):
            if not show_browser:
                restore(context, page)
            page = login(context, username)
            if not show_browser:
                minimize(context, page)
    except Exception:
        context.close()
        raise
    return context, page


def _no_fetch(url: str) -> Fetched:
    raise AssertionError(f"unexpected fetch during dry run: {url}")


def _no_fetch_zip(conversation: str, paths: list[str]) -> Fetched:
    raise AssertionError(f"unexpected zip fetch during dry run: {conversation}")


def _print_summary(report, file_results, artifact_results, generated_results, dry_run: bool) -> None:
    if report is not None:
        print(
            f"conversations: fetched {len(report.fetched)}, "
            f"skipped {report.skipped}, failed {len(report.failed)}"
        )
    file_counts = Counter(result.status for result in file_results)
    artifact_counts = Counter(result.status for result in artifact_results)
    generated_counts = Counter(result.status for result in generated_results)
    if file_counts:
        print(
            ("planned" if dry_run else "files")
            + ": "
            + ", ".join(f"{status} {count}" for status, count in sorted(file_counts.items()))
        )
    if generated_counts:
        print(
            ("planned" if dry_run else "generated")
            + ": "
            + ", ".join(
                f"{status} {count}" for status, count in sorted(generated_counts.items())
            )
        )
    if artifact_counts:
        print(
            ("planned" if dry_run else "artifacts")
            + ": "
            + ", ".join(
                f"{status} {count}" for status, count in sorted(artifact_counts.items())
            )
        )
    if report is not None and report.failed:
        print("failed uuids: " + ", ".join(report.failed), file=sys.stderr)
    issues = [
        result
        for result in file_results
        if result.status in ("failed", "unavailable")
    ]
    if issues:
        print(
            "file issues: "
            + ", ".join(f"{result.ref.file_uuid} ({result.status})" for result in issues),
            file=sys.stderr,
        )
    generated_issues = [
        result for result in generated_results if result.status == "failed"
    ]
    if generated_issues:
        print(
            "generated issues: "
            + ", ".join(
                f"{result.conversation} ({result.reason})"
                for result in generated_issues
            ),
            file=sys.stderr,
        )


def _any_failed(report, *groups) -> bool:
    if report is not None and report.failed:
        return True
    return any(result.status == "failed" for group in groups for result in group)


def _fetch_request(context, url: str) -> Fetched:
    response = context.request.get(url)
    if not response.ok:
        raise DownloadError(f"HTTP {response.status} for {url}")
    return Fetched(response.body(), response.headers.get("content-type"))


def _fetch_generated(context, org: str, conversation: str, paths: list[str]) -> Fetched:
    response = context.request.get(generated_url(org, conversation, paths))
    if not response.ok:
        raise DownloadError(f"HTTP {response.status} for generated files")
    return Fetched(response.body(), response.headers.get("content-type"))


def _sync_with_browser(context, page, args: argparse.Namespace, max_bytes: int | None):
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
                raise SystemExit(1) from None
        else:
            consecutive_failures = 0
        return payload

    refs = collect_conversations(page)
    refresh_uuids = (
        frozenset({args.refresh}) if args.refresh not in (None, "all") else frozenset()
    )
    report = fetch_all(
        refs,
        root=ARCHIVE_ROOT,
        fetch_one=lambda uuid: process_conversation(
            ARCHIVE_ROOT,
            uuid,
            capture,
            lambda pairs: download_media(context, pairs, ARCHIVE_ROOT),
        ),
        force=args.refresh == "all",
        refresh_uuids=refresh_uuids,
    )
    conversations = load_archived(ARCHIVE_ROOT, args.conversation)
    org = read_org(page)
    file_results = process_archived_files(
        ARCHIVE_ROOT,
        conversations,
        lambda url: _fetch_request(context, url),
        max_bytes=max_bytes,
        force=args.force,
    )
    generated_results = (
        process_archived_generated(
            ARCHIVE_ROOT,
            conversations,
            lambda conversation, paths: _fetch_generated(
                context, org, conversation, paths
            ),
            max_bytes=max_bytes,
            force=args.force,
        )
        if org
        else []
    )
    return report, conversations, file_results, generated_results, org


def run_sync(
    args: argparse.Namespace,
    context_factory: Callable[[bool, bool], tuple[Any, Any]] = _open_context,
) -> None:
    if args.conversation is not None and not load_archived(
        ARCHIVE_ROOT, args.conversation
    ):
        print(
            f"conversation not found in archive: {args.conversation}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    max_bytes = int(args.max_size_mb * 1024 * 1024) if args.max_size_mb > 0 else None

    if args.dry_run:
        conversations = load_archived(ARCHIVE_ROOT, args.conversation)
        file_results = process_archived_files(
            ARCHIVE_ROOT,
            conversations,
            _no_fetch,
            max_bytes=max_bytes,
            force=args.force,
            dry_run=True,
        )
        generated_results = process_archived_generated(
            ARCHIVE_ROOT,
            conversations,
            _no_fetch_zip,
            max_bytes=max_bytes,
            force=args.force,
            dry_run=True,
        )
        artifact_results = process_archived_artifacts(
            ARCHIVE_ROOT,
            conversations,
            force=args.force,
            dry_run=True,
        )
        _print_summary(None, file_results, artifact_results, generated_results, True)
        failed = _any_failed(None, file_results, generated_results, artifact_results)
        raise SystemExit(1 if failed else 0)

    try:
        context, page = context_factory(args.login, args.show_browser)
    except MissingUsernameError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    try:
        report, conversations, file_results, generated_results, org = _sync_with_browser(
            context, page, args, max_bytes
        )
    finally:
        context.close()

    if not org:
        print(
            "could not read lastActiveOrg from the browser session; "
            "generated files were not downloaded",
            file=sys.stderr,
        )
    artifact_results = process_archived_artifacts(
        ARCHIVE_ROOT,
        conversations,
        force=args.force,
    )
    atomic_write_json(ARCHIVE_ROOT / "index.json", build_manifest(ARCHIVE_ROOT))
    _print_summary(report, file_results, artifact_results, generated_results, False)
    failed = _any_failed(
        report, file_results, generated_results, artifact_results
    ) or not org
    raise SystemExit(1 if failed else 0)


def main() -> None:
    args = parse_args()
    if args.command == "view":
        from claude_data.curator.server import serve

        serve(
            archive_root=args.archive or ARCHIVE_ROOT,
            data_dir=args.data_dir or Path("curated"),
            port=args.port or 8765,
            open_browser=args.open,
        )
        return
    run_sync(args)


__all__ = ["CaptureError", "main", "parse_args", "run_sync"]