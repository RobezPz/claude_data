import argparse
import sys
from collections import Counter
from pathlib import Path

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
        description="Archive Claude.ai conversations for offline viewing.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["view"],
        default=None,
        help="optional command; 'view' starts the local curator",
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
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="keep the headed browser visible instead of minimizing it",
    )
    parser.add_argument(
        "--download-files",
        action="store_true",
        help="download uploaded and generated files for archived conversations",
    )
    parser.add_argument(
        "--export-artifacts",
        action="store_true",
        help="export inline artifact content for archived conversations",
    )
    parser.add_argument(
        "--conversation",
        metavar="UUID",
        default=None,
        help="limit --download-files/--export-artifacts to one conversation",
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
        help="show planned downloads without writing anything",
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
        "--no-browser",
        action="store_true",
        help="do not open a browser when starting view",
    )
    args = parser.parse_args(argv)
    if args.command == "view":
        archive_flags = (
            args.login
            or args.headed
            or args.refresh is not None
            or args.show_browser
            or args.download_files
            or args.export_artifacts
            or args.conversation is not None
            or args.max_size_mb != 0.0
            or args.force
            or args.dry_run
        )
        if archive_flags:
            parser.error("archive flags cannot be used with view")
        return args
    if (
        args.port is not None
        or args.archive is not None
        or args.data_dir is not None
        or args.no_browser
    ):
        parser.error("--port/--archive/--data-dir/--no-browser require view")
    file_mode = args.download_files or args.export_artifacts
    if file_mode and args.refresh is not None:
        parser.error("--refresh cannot be combined with --download-files/--export-artifacts")
    if args.conversation and not file_mode:
        parser.error("--conversation requires --download-files or --export-artifacts")
    if args.max_size_mb < 0:
        parser.error("--max-size-mb must be zero or positive")
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


def _print_summary(file_results, artifact_results, generated_results, dry_run: bool) -> None:
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


def run_file_tasks(args: argparse.Namespace) -> None:
    conversations = load_archived(ARCHIVE_ROOT, args.conversation)
    if args.conversation and not conversations:
        print(
            f"conversation not found in archive: {args.conversation}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    max_bytes = int(args.max_size_mb * 1024 * 1024) if args.max_size_mb > 0 else None
    file_results = []
    artifact_results = []
    generated_results = []

    if args.download_files:
        if args.dry_run:
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
        else:
            context, page = _open_context(args.login, args.show_browser)
            try:
                org = read_org(page)
                if not org:
                    print(
                        "could not read lastActiveOrg from the browser session",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)

                def fetch(url: str) -> Fetched:
                    response = context.request.get(url)
                    if not response.ok:
                        raise DownloadError(f"HTTP {response.status} for {url}")
                    return Fetched(
                        response.body(), response.headers.get("content-type")
                    )

                def fetch_zip(conversation: str, paths: list[str]) -> Fetched:
                    response = context.request.get(
                        generated_url(org, conversation, paths)
                    )
                    if not response.ok:
                        raise DownloadError(
                            f"HTTP {response.status} for generated files"
                        )
                    return Fetched(
                        response.body(), response.headers.get("content-type")
                    )

                file_results = process_archived_files(
                    ARCHIVE_ROOT,
                    conversations,
                    fetch,
                    max_bytes=max_bytes,
                    force=args.force,
                )
                generated_results = process_archived_generated(
                    ARCHIVE_ROOT,
                    conversations,
                    fetch_zip,
                    max_bytes=max_bytes,
                    force=args.force,
                )
            finally:
                context.close()

    if args.export_artifacts:
        artifact_results = process_archived_artifacts(
            ARCHIVE_ROOT,
            conversations,
            force=args.force,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        atomic_write_json(ARCHIVE_ROOT / "index.json", build_manifest(ARCHIVE_ROOT))

    _print_summary(file_results, artifact_results, generated_results, args.dry_run)
    failed = any(
        result.status == "failed"
        for result in file_results + artifact_results + generated_results
    )
    raise SystemExit(1 if failed else 0)


def main() -> None:
    args = parse_args()
    if args.command == "view":
        from claude_data.curator.server import serve

        serve(
            archive_root=args.archive or ARCHIVE_ROOT,
            data_dir=args.data_dir or Path("curated"),
            port=args.port or 8765,
            open_browser=not args.no_browser,
        )
        return
    if args.download_files or args.export_artifacts:
        run_file_tasks(args)
        return

    try:
        username = require_username(read_env(Path(".env")))
    except MissingUsernameError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    context = launch(headless=False)
    page = context.new_page()
    if not args.show_browser:
        minimize(context, page)
    logged_in = is_logged_in(page)
    if args.login or not logged_in:
        if not args.show_browser:
            restore(context, page)
        page = login(context, username)
        if not args.show_browser:
            minimize(context, page)

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