import pytest

from ajar.errors import DataValidationError, UpstreamRateLimited
from ajar.sources import Himalayas, Jobicy, Remotive, parse_time


class StubHttp:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def request_json(self, method, url, **kwargs):
        self.calls.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def sleep(self, seconds):
        pass


REMOTIVE = {"title": "Data &amp; Ops", "company_name": "A", "url": "https://remotive.com/j/1",
            "candidate_required_location": "USA, Europe",
            "publication_date": "2026-11-20T10:00:00", "description": "<p>Hi <b>there</b></p>"}
JOBICY = {"jobTitle": "Support Agent", "companyName": "B", "url": "https://jobicy.com/jobs/2",
          "jobGeo": "Anywhere", "pubDate": "2026-11-20T10:00:00+00:00",
          "jobLevel": "Junior", "salaryMin": 100, "salaryCurrency": "USD"}
HIMALAYAS = {"title": "VA", "companyName": "C",
             "applicationLink": "https://himalayas.app/c/3", "pubDate": 1795000000,
             "locationRestrictions": [{"alpha2": "NG", "name": "Nigeria"}, "Kenya"],
             "timezoneRestrictions": [1]}


def test_remotive_translation():
    job = Remotive(StubHttp({"jobs": [REMOTIVE]})).fetch()[0]
    assert (job.title, job.description) == ("Data & Ops", "Hi there")
    assert job.locations == ("USA", "Europe")
    assert job.published_at.utcoffset().total_seconds() == 0


def test_jobicy_translation():
    job = Jobicy(StubHttp({"jobs": [JOBICY]})).fetch()[0]
    assert job.seniority == ("Junior",)
    assert job.salary == "USD 100"


def test_himalayas_accepts_both_location_shapes_and_empty_means_worldwide():
    assert Himalayas.parse(HIMALAYAS).locations == ("Nigeria", "Kenya")
    assert Himalayas.parse({**HIMALAYAS, "locationRestrictions": []}).locations == ()
    missing = {k: v for k, v in HIMALAYAS.items() if k != "locationRestrictions"}
    assert Himalayas.parse(missing).locations is None


def test_himalayas_stops_searching_when_rate_limited_but_keeps_results():
    http = StubHttp({"jobs": [HIMALAYAS]}, UpstreamRateLimited("slow down"))
    assert len(Himalayas(http, ["a", "b", "c"]).fetch()) == 1
    assert len(http.calls) == 2


def test_one_bad_record_is_skipped():
    payload = {"jobs": [REMOTIVE, REMOTIVE, {"title": "broken"}]}
    assert len(Remotive(StubHttp(payload)).fetch()) == 2


@pytest.mark.parametrize("payload", [{}, [], {"jobs": "no"}, {"jobs": [{"x": 1}, None]}])
def test_changed_format_is_detected(payload):
    with pytest.raises(DataValidationError) as caught:
        Remotive(StubHttp(payload)).fetch()
    assert caught.value.code == "SOURCE_SHAPE_CHANGED"


def test_unsafe_link_is_rejected():
    with pytest.raises(DataValidationError):
        Remotive.parse({**REMOTIVE, "url": "javascript:alert(1)"})


def test_unreadable_dates_become_none():
    assert parse_time("yesterday") is None
    assert parse_time(None) is None
    assert parse_time(1795000000000) == parse_time(1795000000)
