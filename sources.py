"""Job-board adapters. Each one translates a board's own format into Ajar's Job shape.

To add a board: write a class with name, label, min_interval_hours and fetch(),
then add it to build_sources().
"""

import html
import logging
import re
from datetime import datetime, timezone

from .errors import DataValidationError, UpstreamError
from .logs import log
from .models import Job

# Job boards limit how often we may call, so retry once and slowly.
POLITE = {"attempts": 2, "min_wait": 35}


def strip_html(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def parse_time(value) -> datetime | None:
    """Accept Unix seconds or ISO text. Times without a zone are treated as UTC."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _required(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"missing '{key}'")
    return html.unescape(value).strip()


def _web_link(raw: dict, key: str) -> str:
    link = _required(raw, key)
    if not link.startswith("https://"):
        raise DataValidationError(f"'{key}' is not a secure web link")
    return link


def _strings(value) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    return tuple(str(v) for v in value or [] if v)


def _split_places(text: str) -> tuple[str, ...] | None:
    places = tuple(p.strip() for p in re.split(r"[,;/|]", text or "") if p.strip())
    return places or None


def parse_all(source: str, payload, parser) -> list[Job]:
    """Parse a feed reply, skipping bad records and detecting a changed format."""
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise DataValidationError(f"{source}: no 'jobs' list in reply",
                                  code="SOURCE_SHAPE_CHANGED")
    jobs, bad = [], 0
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise DataValidationError("record is not an object")
            jobs.append(parser(row))
        except (DataValidationError, TypeError, AttributeError, ValueError, KeyError) as exc:
            bad += 1
            if bad <= 5:
                log(logging.WARNING, "bad_record_skipped", source=source, detail=str(exc))
    if rows and bad > len(rows) / 2:
        raise DataValidationError(f"{source}: {bad} of {len(rows)} records unreadable",
                                  code="SOURCE_SHAPE_CHANGED")
    return jobs


class Remotive:
    name, label = "remotive", "Remotive"
    min_interval_hours = 5.0  # their limit: about 4 requests a day

    def __init__(self, http):
        self._http = http

    def fetch(self) -> list[Job]:
        payload = self._http.request_json("GET", "https://remotive.com/api/remote-jobs",
                                          service=self.name, **POLITE)
        return parse_all(self.name, payload, self.parse)

    @staticmethod
    def parse(raw: dict) -> Job:
        place = raw.get("candidate_required_location") or ""
        return Job(source="remotive", title=_required(raw, "title"),
                   company=_required(raw, "company_name"), url=_web_link(raw, "url"),
                   description=strip_html(raw.get("description", "")),
                   published_at=parse_time(raw.get("publication_date")),
                   location_text=place, locations=_split_places(place),
                   salary=raw.get("salary") or "")


class Jobicy:
    name, label = "jobicy", "Jobicy"
    min_interval_hours = 5.0  # their limit: never more than once an hour

    def __init__(self, http):
        self._http = http

    def fetch(self) -> list[Job]:
        payload = self._http.request_json("GET", "https://jobicy.com/api/v2/remote-jobs",
                                          service=self.name, params={"count": 200}, **POLITE)
        return parse_all(self.name, payload, self.parse)

    @staticmethod
    def parse(raw: dict) -> Job:
        place = raw.get("jobGeo") or ""
        salary = ""
        if raw.get("salaryMin"):
            parts = [str(raw["salaryMin"]), str(raw.get("salaryMax") or "")]
            salary = " ".join(filter(None, [
                raw.get("salaryCurrency"), "-".join(filter(None, parts)),
                raw.get("salaryPeriod")]))
        return Job(source="jobicy", title=_required(raw, "jobTitle"),
                   company=_required(raw, "companyName"), url=_web_link(raw, "url"),
                   description=strip_html(raw.get("jobDescription")
                                          or raw.get("jobExcerpt", "")),
                   published_at=parse_time(raw.get("pubDate")),
                   location_text=place, locations=_split_places(place),
                   seniority=_strings(raw.get("jobLevel")), salary=salary)


class Himalayas:
    name, label = "himalayas", "Himalayas"
    min_interval_hours = 20.0  # their data refreshes once a day
    URL = "https://himalayas.app/jobs/api/search"
    MAX_SEARCHES = 12

    def __init__(self, http, searches: list[str]):
        self._http = http
        self._searches = searches[: self.MAX_SEARCHES]

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        failures: list[Exception] = []
        for number, query in enumerate(self._searches):
            if number:
                self._http.sleep(1.5)
            try:
                payload = self._http.request_json(
                    "GET", self.URL, service=self.name,
                    params={"q": query, "country": "NG", "sort": "recent", "page": 1})
                for job in parse_all(self.name, payload, self.parse):
                    jobs[job.url] = job
            except (UpstreamError, DataValidationError) as exc:
                failures.append(exc)
                log(logging.WARNING, "search_failed", source=self.name, query=query,
                    error_code=exc.code)
                if exc.code in ("UPSTREAM_429", "UPSTREAM_AUTH", "CIRCUIT_OPEN"):
                    break  # more searches would only make it worse
        if failures and not jobs:
            raise failures[0]
        return list(jobs.values())

    @staticmethod
    def parse(raw: dict) -> Job:
        restrictions = raw.get("locationRestrictions")
        locations = None
        if isinstance(restrictions, list):
            # The docs show both plain names and objects, so accept either.
            names = [item.get("name") or item.get("alpha2") if isinstance(item, dict)
                     else item for item in restrictions]
            locations = tuple(str(n) for n in names if n)
            if restrictions and not locations:
                locations = None
        salary = ""
        if raw.get("minSalary"):
            top = f"-{raw['maxSalary']}" if raw.get("maxSalary") else ""
            salary = " ".join(filter(None, [
                raw.get("currency"), f"{raw['minSalary']}{top}", raw.get("salaryPeriod")]))
        if locations is None:
            place = ""
        else:
            place = ", ".join(locations[:6]) if locations else "Worldwide"
        return Job(source="himalayas", title=_required(raw, "title"),
                   company=_required(raw, "companyName"),
                   url=_web_link(raw, "applicationLink"),
                   description=strip_html(raw.get("description") or raw.get("excerpt", "")),
                   published_at=parse_time(raw.get("pubDate")),
                   expires_at=parse_time(raw.get("expiryDate")),
                   location_text=place, locations=locations,
                   timezones=tuple(float(z) for z in raw.get("timezoneRestrictions") or []
                                   if isinstance(z, (int, float))),
                   seniority=_strings(raw.get("seniority")), salary=salary)


def build_sources(http, profile: dict) -> list:
    return [Remotive(http), Jobicy(http), Himalayas(http, profile["himalayas_searches"])]
