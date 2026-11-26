"""Data layer: the only file that knows Ajar's database is Supabase."""

import logging
from datetime import datetime

from .errors import DataValidationError
from .logs import log
from .models import Match


def stamp(moment: datetime) -> str:
    return moment.isoformat()


class SupabaseStore:
    def __init__(self, http, url: str, key: str):
        self._http = http
        self._base = f"{url}/rest/v1"
        self._headers = {"apikey": key}
        if key.startswith("eyJ"):  # old-style service_role keys are also bearer tokens
            self._headers["Authorization"] = f"Bearer {key}"

    def _call(self, method, table, *, params=None, body=None, prefer=None, idempotent=True):
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        return self._http.request_json(method, f"{self._base}/{table}", service="supabase",
                                       headers=headers, params=params, body=body,
                                       idempotent=idempotent)

    def _rows(self, table, params) -> list[dict]:
        rows = self._call("GET", table, params=params)
        if not isinstance(rows, list):
            raise DataValidationError(f"supabase: unexpected reply from {table}")
        return rows

    def ping(self) -> None:
        for table in ("jobs", "source_state", "run_log"):
            self._rows(table, {"select": "*", "limit": "1"})

    def source_state(self, name: str) -> dict:
        rows = self._rows("source_state", {"source": f"eq.{name}", "limit": "1"})
        return rows[0] if rows else {}

    def save_source_state(self, name: str, **fields) -> None:
        self._call("POST", "source_state", params={"on_conflict": "source"},
                   body=[{"source": name, **fields}],
                   prefer="resolution=merge-duplicates,return=minimal")
        log(logging.INFO, "db_write", table="source_state", source=name)

    def add_matches(self, matches: list[Match], now: datetime) -> int:
        """Insert unseen jobs. The database ignores ones it already has."""
        if not matches:
            return 0
        rows = [{
            "fingerprint": m.job.fingerprint, "source": m.job.source,
            "title": m.job.title, "company": m.job.company, "url": m.job.url,
            "location_text": m.job.location_text, "salary": m.job.salary,
            "score": m.score, "track": m.track, "reasons": list(m.reasons),
            "warnings": list(m.warnings), "status": "pending",
            "published_at": stamp(m.job.published_at), "first_seen_at": stamp(now),
        } for m in matches]
        inserted = self._call("POST", "jobs", params={"on_conflict": "fingerprint"},
                              body=rows,
                              prefer="resolution=ignore-duplicates,return=representation")
        count = len(inserted or [])
        log(logging.INFO, "db_write", table="jobs", inserted=count, offered=len(rows))
        return count

    def pending_jobs(self, limit: int) -> list[dict]:
        return self._rows("jobs", {"status": "eq.pending",
                                   "order": "score.desc,published_at.desc",
                                   "limit": str(limit)})

    def mark_sent(self, fingerprint: str, now: datetime) -> None:
        self._call("PATCH", "jobs", params={"fingerprint": f"eq.{fingerprint}"},
                   body={"status": "sent", "sent_at": stamp(now)}, prefer="return=minimal")
        log(logging.INFO, "db_write", table="jobs", change="sent")

    def expire_pending(self, published_before: datetime) -> None:
        """Never alert on stale jobs that waited too long in the queue."""
        self._call("PATCH", "jobs",
                   params={"status": "eq.pending",
                           "published_at": f"lt.{stamp(published_before)}"},
                   body={"status": "expired"}, prefer="return=minimal")
        log(logging.INFO, "db_write", table="jobs", change="expire_stale")

    def add_run(self, row: dict) -> None:
        self._call("POST", "run_log", body=[row], prefer="return=minimal",
                   idempotent=False)
        log(logging.INFO, "db_write", table="run_log", status=row["status"])

    def runs_since(self, moment: datetime) -> list[dict]:
        return self._rows("run_log", {"finished_at": f"gte.{stamp(moment)}",
                                      "order": "finished_at.desc", "limit": "200"})

    def last_good_run(self) -> dict | None:
        rows = self._rows("run_log", {"status": "in.(ok,partial)",
                                      "order": "finished_at.desc", "limit": "1"})
        return rows[0] if rows else None

    def prune(self, jobs_before: datetime, runs_before: datetime) -> None:
        """Keep the free database small."""
        self._call("DELETE", "jobs", params={"first_seen_at": f"lt.{stamp(jobs_before)}"},
                   prefer="return=minimal")
        self._call("DELETE", "run_log", params={"finished_at": f"lt.{stamp(runs_before)}"},
                   prefer="return=minimal")
        log(logging.INFO, "db_write", change="prune")
