"""Structured JSON logging: one JSON object per line, with a run ID and secret redaction."""

import json
import logging
import sys
import uuid
from datetime import datetime, timezone

RUN_ID = uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()):
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": "WARN" if record.levelname == "WARNING" else record.levelname,
            "event": record.getMessage(),
            "module": record.module,
            "run_id": RUN_ID,
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception_type"] = record.exc_info[0].__name__
            entry["stack_trace"] = self.formatException(record.exc_info)
        line = json.dumps(entry, default=str)
        for secret in self._secrets:
            line = line.replace(secret, "[REDACTED]")
        return line


def setup_logging(level: str = "INFO", secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    logger = logging.getLogger("ajar")
    logger.handlers = [handler]
    logger.setLevel(level.upper() if level.upper() in logging.getLevelNamesMapping() else "INFO")
    logger.propagate = False


def log(level: int, event: str, *, exc_info: bool = False, **fields) -> None:
    logging.getLogger("ajar").log(level, event, exc_info=exc_info, extra={"fields": fields},
                                  stacklevel=2)
