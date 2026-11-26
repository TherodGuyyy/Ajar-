from datetime import timedelta

import pytest

from ajar.models import Match, Rejection
from ajar.rules import contains, dedupe, evaluate, location_status, years_required

from .helpers import NOW, PROFILE, make_job


def test_good_job_matches_with_reasons():
    result = evaluate(make_job(), PROFILE, NOW)
    assert isinstance(result, Match)
    assert result.score >= 80
    assert "Location rules look open to Nigeria" in result.reasons


@pytest.mark.parametrize("locations,expected", [
    ((), "open"), (("Worldwide",), "open"), (("Anywhere",), "open"),
    (("Nigeria", "Ghana"), "open"), (("Africa",), "open"),
    (("USA",), "closed"), (("South Africa",), "closed"),
    (("Anywhere in the US",), "closed"), (("EMEA",), "maybe"),
    (("Remote",), "unclear"), (None, "unclear"),
])
def test_location_status(locations, expected):
    assert location_status(make_job(locations=locations)) == expected


@pytest.mark.parametrize("changes,reason", [
    ({"published_at": None}, "no_publish_date"),
    ({"published_at": NOW - timedelta(days=30)}, "too_old"),
    ({"expires_at": NOW - timedelta(hours=1)}, "expired"),
    ({"title": "Senior Python Developer"}, "excluded_title"),
    ({"seniority": ("Senior",)}, "too_senior"),
    ({"locations": ("USA",)}, "location_closed"),
    ({"description": "You must reside in the United States."}, "blocking_phrase"),
    ({"description": "7+ years of experience with Python."}, "too_much_experience"),
    ({"title": "Forklift Driver"}, "no_matching_role"),
    ({"locations": None, "title": "Data Analyst", "description": "Join us."}, "low_score"),
])
def test_rejections(changes, reason):
    assert evaluate(make_job(**changes), PROFILE, NOW) == Rejection(reason)


def test_whole_word_matching():
    assert contains("Junior-level AI role", "ai")
    assert not contains("international team, maintain servers", "intern")
    assert not contains("maintain", "ai")


@pytest.mark.parametrize("text,years", [
    ("5+ years of experience", 5), ("3-5 years of professional experience", 3),
    ("at least 2 years of customer support experience", 2),
    ("We have been in business for 10 years.", 0),
])
def test_years_required(text, years):
    assert years_required(text) == years


def test_experience_and_timezone_lower_the_score():
    plain = evaluate(make_job(), PROFILE, NOW)
    harder = evaluate(make_job(description="Python, GitHub and APIs. "
                               "3+ years of experience.", timezones=(-8.0,)), PROFILE, NOW)
    assert harder.score < plain.score
    assert len(harder.warnings) == 2


def test_same_job_on_two_boards_is_one_alert():
    a = evaluate(make_job(), PROFILE, NOW)
    b = evaluate(make_job(source="jobicy", company="ACME", locations=None), PROFILE, NOW)
    assert dedupe([b, a]) == [a]
