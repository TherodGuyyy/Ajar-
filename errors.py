"""Ajar's own error types. Every error carries a stable code listed in RUNBOOK.md."""


class AjarError(Exception):
    code = "AJAR_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class ConfigError(AjarError):
    code = "CONFIG_INVALID"


class UpstreamError(AjarError):
    """An outside service failed."""

    code = "UPSTREAM_FAILED"


class UpstreamUnavailable(UpstreamError):
    code = "UPSTREAM_DOWN"


class UpstreamRateLimited(UpstreamError):
    code = "UPSTREAM_429"


class UpstreamAuthFailed(UpstreamError):
    code = "UPSTREAM_AUTH"


class UpstreamRejected(UpstreamError):
    code = "UPSTREAM_REJECTED"


class CircuitOpen(UpstreamError):
    code = "CIRCUIT_OPEN"


class DataValidationError(AjarError):
    code = "DATA_INVALID"
