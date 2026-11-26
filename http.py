"""One safe doorway for every network call: timeouts, backoff, rate limits, circuit breaker."""

import logging
import random
import time
from collections.abc import Callable

import requests

from .errors import (CircuitOpen, DataValidationError, UpstreamAuthFailed, UpstreamError,
                     UpstreamRateLimited, UpstreamRejected, UpstreamUnavailable)
from .logs import log

TIMEOUT_SECONDS = 25
MAX_ATTEMPTS = 4
MAX_RETRY_AFTER = 60


class CircuitBreaker:
    """After repeated failures, stop calling a service for a cool-down period."""

    def __init__(self, threshold: int = 3, cooldown: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def check(self, service: str) -> None:
        opened = self._opened_at.get(service)
        if opened is None:
            return
        if self._clock() - opened < self.cooldown:
            raise CircuitOpen(f"{service} is cooling down after repeated failures")
        del self._opened_at[service]
        self._failures[service] = self.threshold - 1  # half-open: one trial call
        log(logging.INFO, "circuit_half_open", service=service)

    def success(self, service: str) -> None:
        if self._failures.pop(service, 0) >= self.threshold - 1:
            log(logging.INFO, "circuit_closed", service=service)

    def failure(self, service: str) -> None:
        self._failures[service] = self._failures.get(service, 0) + 1
        if self._failures[service] >= self.threshold:
            self._opened_at[service] = self._clock()
            log(logging.WARNING, "circuit_opened", service=service,
                failures=self._failures[service])


def _retry_after(response) -> float | None:
    """Read the wait time from the header, or from Telegram's JSON body."""
    header = response.headers.get("Retry-After")
    if header and header.isdigit():
        return float(header)
    try:
        return float(response.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return None


class HttpClient:
    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = random.random,
                 breaker: CircuitBreaker | None = None):
        self._session = session or requests.Session()
        self.sleep = sleep
        self._jitter = jitter
        self._breaker = breaker or CircuitBreaker()

    def request_json(self, method: str, url: str, *, service: str, idempotent: bool = True,
                     headers: dict | None = None, params: dict | None = None, body=None,
                     attempts: int = MAX_ATTEMPTS, min_wait: float = 0):
        """Return decoded JSON (or None for an empty reply).

        URLs are never logged because the Telegram URL contains the bot token.
        Set idempotent=False when repeating a call could create a duplicate.
        """
        self._breaker.check(service)
        try:
            result = self._attempts(method, url, service, idempotent, headers, params, body,
                                    attempts, min_wait)
        except (UpstreamUnavailable, UpstreamRateLimited):
            self._breaker.failure(service)
            raise
        self._breaker.success(service)
        return result

    def _attempts(self, method, url, service, idempotent, headers, params, body,
                  max_attempts, min_wait):
        sent_headers = {"User-Agent": "Ajar/1.0 (personal job alert tool)", **(headers or {})}
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            wait: float = 2 ** (attempt - 1) + self._jitter()
            error: UpstreamError
            retryable = idempotent
            try:
                response = self._session.request(method, url, headers=sent_headers,
                                                 params=params, json=body,
                                                 timeout=TIMEOUT_SECONDS)
            except requests.exceptions.ConnectTimeout:
                error, retryable = UpstreamUnavailable(f"{service}: connect timeout"), True
            except requests.exceptions.Timeout:
                error = UpstreamUnavailable(f"{service}: timed out")
            except requests.exceptions.ConnectionError:
                error = UpstreamUnavailable(f"{service}: connection failed")
            except requests.exceptions.RequestException as exc:
                raise UpstreamRejected(f"{service}: {type(exc).__name__}") from None
            else:
                status = response.status_code
                log(logging.INFO, "external_call", service=service, method=method,
                    status=status, attempt=attempt,
                    duration_ms=int((time.monotonic() - started) * 1000))
                if status < 300:
                    return self._decode(response, service)
                if status in (401, 403):
                    raise UpstreamAuthFailed(f"{service}: access refused (HTTP {status})")
                if status == 429:
                    requested = _retry_after(response)
                    error, retryable = UpstreamRateLimited(f"{service}: rate limited"), True
                    if requested is not None:
                        if requested > MAX_RETRY_AFTER:
                            raise error
                        wait = requested
                elif status >= 500:
                    error = UpstreamUnavailable(f"{service}: server error (HTTP {status})")
                else:
                    raise UpstreamRejected(f"{service}: request rejected (HTTP {status})")

            if not retryable or attempt == max_attempts:
                log(logging.ERROR, "external_call_failed", service=service,
                    error_code=error.code, detail=str(error), attempts=attempt)
                raise error
            wait = max(wait, min_wait)  # some services ask for slower retries
            log(logging.WARNING, "retrying", service=service, error_code=error.code,
                attempt=attempt, wait_seconds=round(wait, 2))
            self.sleep(wait)

    @staticmethod
    def _decode(response, service):
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise DataValidationError(f"{service}: reply was not valid JSON",
                                      code="SOURCE_SHAPE_CHANGED") from None
