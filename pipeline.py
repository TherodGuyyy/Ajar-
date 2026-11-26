"""The conductor: fetch -> judge -> remember -> alert -> self-check.

Everything it uses is handed in (dependency injection), so tests can pass in fakes.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

from .errors import AjarError, UpstreamError, UpstreamRejected
from .logs import RUN_ID, log
from .rules import dedupe, evaluate
from .sources import parse_time
from .store import stamp

FAILURE_ALERT_AFTER = 3
FAILURE_REMINDER_EVERY = 12
SILENCE_LIMIT = timedelta(hours=18)
KEEP_JOBS = timedelta(days=180)
KEEP_RUNS = timedelta(days=30)


class Pipeline:
    def __init__(self, sources: list, store, notifier, profile: dict,
                 clock: Callable[[], datetime]):
        self.sources, self.store, self.notifier = sources, store, notifier
        self.profile, self.clock = profile, clock

    def run(self) -> dict:
        started = self.clock()
        errors: Counter = Counter()
        rejected: Counter = Counter()
        stats = {"fetched": 0, "matched": 0, "new": 0, "sent": 0}
        log(logging.INFO, "run_started")
        self._check_silence(started, errors)

        matches, failed, succeeded = [], 0, 0
        for source in self.sources:
            try:
                jobs = self._fetch(source, errors)
            except AjarError as exc:
                failed += 1
                errors[exc.code] += 1
                log(logging.ERROR, "source_failed", source=source.name,
                    error_code=exc.code, detail=str(exc), exc_info=True)
                continue
            if jobs is None:
                continue
            succeeded += 1
            stats["fetched"] += len(jobs)
            for job in jobs:
                result = evaluate(job, self.profile, started)
                if hasattr(result, "job"):
                    matches.append(result)
                else:
                    rejected[result.reason] += 1

        unique = dedupe(matches)
        stats["matched"] = len(unique)
        stats["new"] = self.store.add_matches(unique, started)
        self._deliver(stats, errors)

        if failed and not succeeded:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "ok"
        stats.update(errors=dict(errors), rejected=dict(rejected),
                     sources_ok=succeeded, sources_failed=failed)
        self._finish(started, status, stats)
        log(logging.INFO, "run_finished", status=status, **stats)
        return {"status": status, **stats}

    def _fetch(self, source, errors: Counter):
        """Return jobs, or None if the board was asked too recently to ask again."""
        now = self.clock()
        state = self.store.source_state(source.name)
        last = parse_time(state.get("last_attempt_at"))
        if last and now - last < timedelta(hours=source.min_interval_hours):
            log(logging.INFO, "source_skipped_rate_budget", source=source.name)
            return None
        # Recorded BEFORE the call, so a crash still counts against the board's limit.
        self.store.save_source_state(source.name, last_attempt_at=stamp(now))
        try:
            jobs = source.fetch()
            if not jobs and source.name != "himalayas":
                raise UpstreamError(f"{source.name}: feed returned no jobs",
                                    code="SOURCE_EMPTY")
        except AjarError as exc:
            count = int(state.get("consecutive_failures") or 0) + 1
            self.store.save_source_state(source.name, consecutive_failures=count,
                                         last_error_code=exc.code)
            urgent = exc.code in ("UPSTREAM_AUTH", "SOURCE_SHAPE_CHANGED") and count == 1
            if (urgent or count == FAILURE_ALERT_AFTER
                    or count % FAILURE_REMINDER_EVERY == 0):
                self._warn(f"⚠️ Ajar: {source.label} has failed {count} time(s). "
                           f"Code {exc.code}. See RUNBOOK.md.", errors)
            raise
        self.store.save_source_state(source.name, consecutive_failures=0,
                                     last_error_code=None, last_success_at=stamp(now),
                                     last_count=len(jobs))
        log(logging.INFO, "source_fetched", source=source.name, jobs=len(jobs))
        return jobs

    def _deliver(self, stats: dict, errors: Counter) -> None:
        now = self.clock()
        self.store.expire_pending(now - timedelta(days=self.profile["max_age_days"]))
        for row in self.store.pending_jobs(self.profile["max_alerts_per_run"]):
            try:
                self.notifier.send_job(row)
            except UpstreamRejected as exc:
                errors["TELEGRAM_REJECTED"] += 1
                log(logging.ERROR, "alert_rejected", fingerprint=row["fingerprint"],
                    detail=str(exc))
                continue
            except UpstreamError as exc:
                errors[exc.code] += 1
                log(logging.ERROR, "alerts_stopped", error_code=exc.code, detail=str(exc))
                break  # unsent jobs stay 'pending' and go out next run
            # If this write fails, the run stops here, so at most one alert can repeat.
            self.store.mark_sent(row["fingerprint"], now)
            stats["sent"] += 1

    def _check_silence(self, now: datetime, errors: Counter) -> None:
        last = self.store.last_good_run()
        finished = parse_time(last.get("finished_at")) if last else None
        if finished and now - finished > SILENCE_LIMIT:
            hours = int((now - finished).total_seconds() // 3600)
            log(logging.WARNING, "silence_detected", error_code="RUN_GAP", hours=hours)
            self._warn(f"⚠️ Ajar: no successful run for {hours} hours. Running again now.",
                       errors)

    def _finish(self, started: datetime, status: str, stats: dict) -> None:
        """Send the daily health report if due, record this run, tidy old rows."""
        now = self.clock()
        recent = self.store.runs_since(now - timedelta(hours=23))
        if not any((r.get("stats") or {}).get("digest_sent") for r in recent):
            runs = [(r.get("stats") or {}) for r in recent] + [stats]
            total_errors: Counter = Counter()
            for run in runs:
                total_errors.update(run.get("errors") or {})
            summary = ", ".join(f"{k} ×{v}" for k, v in total_errors.items()) or "none"
            text = ("💓 Ajar daily health report\n"
                    f"Runs: {len(runs)}\n"
                    f"Jobs checked: {sum(r.get('fetched', 0) for r in runs)}\n"
                    f"Alerts sent: {sum(r.get('sent', 0) for r in runs)}\n"
                    f"Errors: {summary}")
            if self._warn(text, Counter()):
                stats["digest_sent"] = True
        self.store.add_run({"run_id": RUN_ID, "started_at": stamp(started),
                            "finished_at": stamp(now), "status": status, "stats": stats})
        if stats.get("digest_sent"):  # tidy once a day, after the run is safely recorded
            self.store.prune(now - KEEP_JOBS, now - KEEP_RUNS)

    def _warn(self, text: str, errors: Counter) -> bool:
        """Send a system message. A Telegram outage must not stop the run."""
        try:
            self.notifier.send_text(text)
        except UpstreamError as exc:
            errors[exc.code] += 1
            log(logging.ERROR, "system_message_failed", error_code=exc.code)
            return False
        return True
