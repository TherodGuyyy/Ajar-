"""The shapes of data that move through Ajar."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Job:
    source: str
    title: str
    company: str
    url: str
    description: str
    published_at: datetime | None
    location_text: str = ""
    # None = the feed did not say. Empty tuple = the feed says "no restriction".
    locations: tuple[str, ...] | None = None
    timezones: tuple[float, ...] = ()
    seniority: tuple[str, ...] = ()
    expires_at: datetime | None = None
    salary: str = ""

    @property
    def fingerprint(self) -> str:
        """Same company + same title = same job, even across two job boards."""
        def clean(text: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

        key = f"{clean(self.company)}|{clean(self.title)}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Match:
    job: Job
    score: int
    track: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Rejection:
    reason: str
