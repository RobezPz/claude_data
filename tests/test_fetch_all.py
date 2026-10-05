from claude_data.collect import ConversationRef
from claude_data.fetch import CaptureError, FetchReport, fetch_all


def make_ref(uuid):
    return ConversationRef(uuid=uuid, title="", created_at="", updated_at="")


def test_fetch_all_skips_archived_and_resumes_missing(tmp_path):
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "done.json").write_text("{}")
    calls = []
    report = fetch_all(
        [make_ref("done"), make_ref("todo")],
        tmp_path,
        fetch_one=lambda uuid: calls.append(uuid),
    )
    assert calls == ["todo"]
    assert report == FetchReport(fetched=["todo"], failed=[], skipped=1)


def test_fetch_all_refresh_and_force_refetch_existing(tmp_path):
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "done.json").write_text("{}")
    calls = []
    report = fetch_all(
        [make_ref("done")],
        tmp_path,
        fetch_one=lambda u: calls.append(u) or None,
        refresh_uuids=frozenset({"done"}),
    )
    assert calls == ["done"]
    report = fetch_all(
        [make_ref("done")],
        tmp_path,
        fetch_one=lambda u: calls.append(u) or None,
        force=True,
    )
    assert calls == ["done", "done"] and report.skipped == 0


def test_fetch_all_capture_failure_retries_then_skips(tmp_path):
    attempts = []

    def failing(uuid):
        attempts.append(uuid)
        raise CaptureError(uuid)

    report = fetch_all([make_ref("bad")], tmp_path, fetch_one=failing)
    assert len(attempts) == 3
    assert report.failed == ["bad"] and report.fetched == []


def test_fetch_all_success_after_one_retry(tmp_path):
    calls = []

    def flaky(uuid):
        calls.append(uuid)
        if len(calls) == 1:
            raise CaptureError(uuid)

    report = fetch_all([make_ref("u")], tmp_path, fetch_one=flaky)
    assert report.fetched == ["u"] and report.failed == []
    assert len(calls) == 2