"""Fakes that stand in for the internet, the database and Telegram."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ajar.config import load_profile
from ajar.models import Job

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
PROFILE = load_profile(Path(__file__).resolve().parent.parent / "profile.json")


def make_job(**changes) -> Job:
    base = dict(source="remotive", title="Junior Python Developer", company="Acme",
                url="https://example.com/job/1",
                description="Build automation with Python, GitHub and APIs.",
                published_at=NOW - timedelta(days=1), location_text="Worldwide",
                locations=("Worldwide",))
    base.update(changes)
    return Job(**base)


class FakeResponse:
    def __init__(self, status=200, data=None, headers=None, raw: bytes | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.content = raw if raw is not None else (
            b"" if data is None else json.dumps(data).encode())

    def json(self):
        return json.loads(self.content)


class FakeSession:
    """Plays back a script of replies; an exception in the script gets raised."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStore:
    def __init__(self):
        self.jobs, self.states, self.runs, self.pruned = {}, {}, [], 0

    def source_state(self, name):
        return dict(self.states.get(name, {}))

    def save_source_state(self, name, **fields):
        self.states.setdefault(name, {}).update(fields)

    def add_matches(self, matches, now):
        new = 0
        for m in matches:
            if m.job.fingerprint not in self.jobs:
                new += 1
                self.jobs[m.job.fingerprint] = dict(
                    fingerprint=m.job.fingerprint, source=m.job.source,
                    title=m.job.title, company=m.job.company, url=m.job.url,
                    score=m.score, track=m.track, reasons=list(m.reasons),
                    warnings=list(m.warnings), status="pending")
        return new

    def pending_jobs(self, limit):
        rows = [r for r in self.jobs.values() if r["status"] == "pending"]
        return sorted(rows, key=lambda r: -r["score"])[:limit]

    def mark_sent(self, fingerprint, now):
        self.jobs[fingerprint]["status"] = "sent"

    def expire_pending(self, before):
        pass

    def add_run(self, row):
        self.runs.append(row)

    def runs_since(self, moment):
        return [r for r in self.runs if r["finished_at"] >= moment.isoformat()]

    def last_good_run(self):
        good = [r for r in self.runs if r["status"] != "failed"]
        return good[-1] if good else None

    def prune(self, jobs_before, runs_before):
        self.pruned += 1


class FakeNotifier:
    def __init__(self, job_error=None):
        self.sent, self.texts, self.job_error = [], [], job_error

    def send_job(self, row):
        if self.job_error:
            raise self.job_error
        self.sent.append(row)

    def send_text(self, text, **_):
        self.texts.append(text)


class FakeSource:
    min_interval_hours = 5.0

    def __init__(self, name, jobs=(), error=None):
        self.name = self.label = name
        self._jobs, self._error, self.calls = list(jobs), error, 0

    def fetch(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._jobs
