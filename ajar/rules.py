"""Business logic only: can this person get through the door, and how good is the fit?

No network, no database. Same input always gives the same answer.
"""

import re
from datetime import datetime, timedelta

from .models import Job, Match, Rejection

OPEN_PLACES = ("worldwide", "anywhere", "global", "africa", "nigeria", "ng")
MAYBE_PLACES = ("emea",)
VAGUE_PLACES = ("remote", "fully remote", "100% remote", "flexible")
SENIOR_LEVELS = ("senior", "manager", "director", "executive", "lead", "principal")
JUNIOR_PHRASES = ("entry level", "entry-level", "junior", "no experience", "internship",
                  "intern", "trainee", "graduate")
HOME_UTC_OFFSET = 1  # Nigeria
YEARS_PATTERN = re.compile(
    r"(\d{1,2})\s*(?:\+|(?:-|–|to)\s*\d{1,2}\s*\+?)?\s*years?['’]?"
    r"(?:\s+of)?(?:\s+[\w/&-]+){0,4}?\s+experience", re.IGNORECASE)


def contains(text: str, phrase: str) -> bool:
    """Whole-word match, so 'intern' does not match 'international'."""
    pattern = rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def location_status(job: Job) -> str:
    """Return 'open', 'maybe', 'unclear' or 'closed' for someone living in Nigeria."""
    if job.locations is None:
        return "unclear"
    if not job.locations:
        return "open"
    joined = " | ".join(job.locations).lower()
    # "Anywhere in the US" or "South Africa" must not count as open.
    narrowed = re.search(r"(anywhere|worldwide|global\w*)\s+(in|within)\s+", joined)
    joined = joined.replace("south africa", "")
    if not narrowed and any(contains(joined, p) for p in OPEN_PLACES):
        return "open"
    if any(contains(joined, p) for p in MAYBE_PLACES):
        return "maybe"
    if all(p.lower() in VAGUE_PLACES for p in job.locations):
        return "unclear"
    return "closed"


def years_required(description: str) -> int:
    """Highest 'N years of experience' asked for. For a range such as 3-5, the lower end."""
    found = [int(m) for m in YEARS_PATTERN.findall(description)]
    return max((y for y in found if y <= 20), default=0)


def _best_track(job: Job, tracks: dict):
    """A track only counts if the job TITLE matches it. This keeps alerts relevant."""
    best = None
    for track in tracks.values():
        title_hit = next((t for t in track["titles"] if contains(job.title, t)), None)
        if not title_hit:
            continue
        text = f"{job.title} {job.description}"
        skills = [s for s in track["skills"] if contains(text, s)]
        if best is None or len(skills) > len(best[2]):
            best = (track, title_hit, skills)
    return best


def evaluate(job: Job, profile: dict, now: datetime) -> Match | Rejection:
    if job.published_at is None:
        return Rejection("no_publish_date")
    if now - job.published_at > timedelta(days=profile["max_age_days"]):
        return Rejection("too_old")
    if job.expires_at and job.expires_at <= now:
        return Rejection("expired")
    if any(contains(job.title, w) for w in profile["excluded_title_words"]):
        return Rejection("excluded_title")
    if job.seniority and all(any(s in level.lower() for s in SENIOR_LEVELS)
                             for level in job.seniority):
        return Rejection("too_senior")

    place = location_status(job)
    if place == "closed":
        return Rejection("location_closed")
    if any(contains(job.description, p) for p in profile["blocking_phrases"]):
        return Rejection("blocking_phrase")

    years = years_required(job.description)
    if years > profile["max_years_required"]:
        return Rejection("too_much_experience")

    found = _best_track(job, profile["tracks"])
    if found is None:
        return Rejection("no_matching_role")
    track, title_hit, skills = found

    score, reasons, warnings = 50, [f"Title matches '{title_hit}'"], []
    if skills:
        score += min(len(skills) * 6, 30)
        reasons.append("Skills: " + ", ".join(skills[:5]))
    if place == "open":
        score += 15
        reasons.append("Location rules look open to Nigeria")
    elif place == "maybe":
        score += 5
        warnings.append("Says EMEA: confirm Africa is included")
    else:
        warnings.append("Location rules unclear: check before applying")
    entry_text = f"{job.title} {' '.join(job.seniority)} {job.description}"
    if any(contains(entry_text, p) for p in JUNIOR_PHRASES) or any(
            "entry" in level.lower() for level in job.seniority):
        score += 10
        reasons.append("Beginner friendly")
    if now - job.published_at <= timedelta(days=3):
        score += 5
        reasons.append("Posted in the last 3 days")
    if years >= 3:
        score -= 20
        warnings.append(f"Asks for about {years} years of experience")
    if job.timezones and min(abs(z - HOME_UTC_OFFSET) for z in job.timezones) > 2:
        score -= 10
        warnings.append("Working hours are far from Nigeria time")

    score = max(0, min(score, 100))
    if score < profile["min_score"]:
        return Rejection("low_score")
    return Match(job, score, track["label"], tuple(reasons), tuple(warnings))


def dedupe(matches: list[Match]) -> list[Match]:
    """Keep the best-scoring copy of each job, best first."""
    best: dict[str, Match] = {}
    for match in matches:
        key = match.job.fingerprint
        if key not in best or match.score > best[key].score:
            best[key] = match
    return sorted(best.values(), key=lambda m: m.score, reverse=True)
