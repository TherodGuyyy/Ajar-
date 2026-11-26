"""Integration tests: the whole run, with fakes at the outer edges."""

from datetime import timedelta

from ajar.errors import UpstreamAuthFailed, UpstreamUnavailable
from ajar.pipeline import Pipeline

from .helpers import (NOW, PROFILE, FakeNotifier, FakeSource, FakeStore, make_job)


def build(sources, store=None, notifier=None, now=NOW):
    store = store or FakeStore()
    notifier = notifier or FakeNotifier()
    return Pipeline(sources, store, notifier, PROFILE, clock=lambda: now), store, notifier


def test_full_run_sends_once_and_never_repeats():
    pipeline, store, notifier = build([FakeSource("remotive", [make_job()])])
    assert pipeline.run()["sent"] == 1
    later, _, _ = build([FakeSource("remotive", [make_job()])], store, notifier,
                        NOW + timedelta(hours=6))
    assert later.run()["sent"] == 0
    assert len(notifier.sent) == 1


def test_one_broken_board_does_not_stop_the_others():
    pipeline, _, notifier = build([
        FakeSource("jobicy", error=UpstreamUnavailable("down")),
        FakeSource("remotive", [make_job()])])
    result = pipeline.run()
    assert result["status"] == "partial"
    assert len(notifier.sent) == 1


def test_all_boards_down_is_a_failed_run():
    pipeline, _, _ = build([FakeSource("jobicy", error=UpstreamUnavailable("down"))])
    assert pipeline.run()["status"] == "failed"


def test_telegram_outage_keeps_jobs_waiting():
    broken = FakeNotifier(job_error=UpstreamUnavailable("telegram down"))
    pipeline, store, _ = build([FakeSource("remotive", [make_job()])], notifier=broken)
    assert pipeline.run()["sent"] == 0
    assert [r["status"] for r in store.jobs.values()] == ["pending"]


def test_alert_cap_per_run():
    jobs = [make_job(company=f"C{i}", url=f"https://e.com/{i}") for i in range(25)]
    pipeline, _, notifier = build([FakeSource("remotive", jobs)])
    pipeline.run()
    assert len(notifier.sent) == PROFILE["max_alerts_per_run"]


def test_board_is_not_asked_again_too_soon():
    source = FakeSource("remotive", [make_job()])
    pipeline, store, notifier = build([source])
    pipeline.run()
    build([source], store, notifier, NOW + timedelta(hours=1))[0].run()
    assert source.calls == 1


def test_warns_on_third_failure_in_a_row():
    store, notifier = FakeStore(), FakeNotifier()
    for i in range(3):
        build([FakeSource("jobicy", error=UpstreamUnavailable("down"))],
              store, notifier, NOW + timedelta(hours=6 * i))[0].run()
    assert sum("failed 3 time" in t for t in notifier.texts) == 1


def test_key_problem_warns_immediately():
    pipeline, _, notifier = build([FakeSource("jobicy", error=UpstreamAuthFailed("no"))])
    pipeline.run()
    assert any("UPSTREAM_AUTH" in t for t in notifier.texts)


def test_empty_feed_counts_as_a_failure():
    pipeline, store, _ = build([FakeSource("remotive", [])])
    pipeline.run()
    assert store.states["remotive"]["last_error_code"] == "SOURCE_EMPTY"


def test_silence_is_reported():
    store = FakeStore()
    store.runs.append({"finished_at": (NOW - timedelta(hours=40)).isoformat(),
                       "status": "ok", "stats": {}})
    pipeline, _, notifier = build([], store)
    pipeline.run()
    assert any("no successful run for 40 hours" in t for t in notifier.texts)


def test_health_report_is_sent_once_a_day():
    store, notifier = FakeStore(), FakeNotifier()
    for hours in (0, 6, 12, 18, 24):
        build([], store, notifier, NOW + timedelta(hours=hours))[0].run()
    assert sum("health report" in t for t in notifier.texts) == 2
    assert store.pruned == 2
