import pytest
import requests

from ajar.errors import (CircuitOpen, DataValidationError, UpstreamAuthFailed,
                         UpstreamRateLimited, UpstreamRejected, UpstreamUnavailable)
from ajar.http import CircuitBreaker, HttpClient

from .helpers import FakeResponse, FakeSession


def client(*script, breaker=None):
    waits = []
    session = FakeSession(*script)
    return HttpClient(session, sleep=waits.append, jitter=lambda: 0,
                      breaker=breaker), session, waits


def call(http, **kwargs):
    return http.request_json("GET", "https://example.com", service="x", **kwargs)


def test_backoff_doubles_then_succeeds():
    http, _, waits = client(FakeResponse(500), requests.exceptions.ConnectionError(),
                            FakeResponse(200, {"ok": 1}))
    assert call(http) == {"ok": 1}
    assert waits == [1, 2]


def test_rate_limit_obeys_retry_after_header_and_telegram_body():
    http, _, waits = client(FakeResponse(429, headers={"Retry-After": "7"}),
                            FakeResponse(429, {"parameters": {"retry_after": 3}}),
                            FakeResponse(200, {}))
    call(http, idempotent=False)
    assert waits == [7, 3]


def test_very_long_retry_after_gives_up_at_once():
    http, session, _ = client(FakeResponse(429, headers={"Retry-After": "3600"}))
    with pytest.raises(UpstreamRateLimited):
        call(http)
    assert len(session.calls) == 1


@pytest.mark.parametrize("status,error", [(401, UpstreamAuthFailed),
                                          (403, UpstreamAuthFailed),
                                          (404, UpstreamRejected)])
def test_client_errors_are_not_retried(status, error):
    http, session, _ = client(FakeResponse(status))
    with pytest.raises(error):
        call(http)
    assert len(session.calls) == 1


def test_non_idempotent_call_is_not_repeated_after_read_timeout():
    http, session, _ = client(requests.exceptions.ReadTimeout())
    with pytest.raises(UpstreamUnavailable):
        call(http, idempotent=False)
    assert len(session.calls) == 1


def test_bad_json_means_shape_changed():
    http, _, _ = client(FakeResponse(200, raw=b"<html>oops</html>"))
    with pytest.raises(DataValidationError) as caught:
        call(http)
    assert caught.value.code == "SOURCE_SHAPE_CHANGED"


def test_circuit_opens_then_allows_a_trial_after_cooldown():
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, cooldown=60, clock=lambda: now[0])
    http, session, _ = client(*[FakeResponse(500)] * 8, FakeResponse(200, {}),
                              breaker=breaker)
    for _ in range(2):
        with pytest.raises(UpstreamUnavailable):
            call(http)
    with pytest.raises(CircuitOpen):
        call(http)
    assert len(session.calls) == 8
    now[0] = 61
    assert call(http) == {}


def test_job_board_retries_stay_under_two_calls_a_minute():
    """Regression: 1s, 2s, 4s retries could break Remotive's 2-per-minute limit."""
    http, session, waits = client(FakeResponse(503), FakeResponse(503))
    with pytest.raises(UpstreamUnavailable):
        call(http, attempts=2, min_wait=35)
    assert len(session.calls) == 2 and waits == [35]
