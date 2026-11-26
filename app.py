"""Entry point. This is where the real parts are built and plugged together.

    python -m ajar.app doctor   check secrets, database and Telegram
    python -m ajar.app run      search once and send alerts
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import load_profile, load_secrets
from .errors import AjarError, ConfigError, UpstreamError
from .http import HttpClient
from .logs import log, setup_logging
from .pipeline import Pipeline
from .sources import build_sources
from .store import SupabaseStore
from .telegram import Telegram

PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile.json"


def doctor(store, notifier) -> int:
    """Test each connection separately so one failure does not hide another."""
    problems = 0
    checks = (("database", store.ping),
              ("telegram_bot", notifier.check),
              ("telegram_chat", lambda: notifier.send_text(
                  "🩺 Ajar doctor: your Telegram connection works.")))
    for name, check in checks:
        try:
            check()
            log(logging.INFO, "doctor_check_passed", check=name)
        except AjarError as exc:
            problems += 1
            log(logging.ERROR, "doctor_check_failed", check=name, error_code=exc.code,
                detail=str(exc))
    log(logging.INFO, "doctor_finished", problems=problems)
    return 1 if problems else 0


def main(argv: list[str] | None = None, env=None) -> int:
    parser = argparse.ArgumentParser(prog="ajar")
    parser.add_argument("command", choices=["run", "doctor"])
    command = parser.parse_args(argv).command
    env = os.environ if env is None else env
    setup_logging(env.get("LOG_LEVEL") or "INFO")

    if env.get("AJAR_PAUSED", "").lower() == "true":
        log(logging.WARNING, "paused", detail="AJAR_PAUSED is true; nothing was done")
        return 0

    notifier = None
    try:
        secrets = load_secrets(env)
        setup_logging(env.get("LOG_LEVEL") or "INFO", secrets.sensitive_values())
        http = HttpClient()
        notifier = Telegram(http, secrets.telegram_token, secrets.telegram_chat_id)
        store = SupabaseStore(http, secrets.supabase_url, secrets.supabase_key)
        if command == "doctor":
            return doctor(store, notifier)
        profile = load_profile(PROFILE_PATH)
        pipeline = Pipeline(build_sources(http, profile), store, notifier, profile,
                            clock=lambda: datetime.now(timezone.utc))
        return 1 if pipeline.run()["status"] == "failed" else 0
    except ConfigError as exc:
        log(logging.ERROR, "config_error", error_code=exc.code, detail=str(exc))
        _last_words(notifier, exc.code)
        return 2
    except Exception as exc:  # noqa: BLE001 - the one allowed catch-all: outer boundary
        code = getattr(exc, "code", "UNEXPECTED_CRASH")
        log(logging.ERROR, "run_crashed", error_code=code, exc_info=True)
        _last_words(notifier, code)
        return 1


def _last_words(notifier, code: str) -> None:
    if notifier is None:
        return
    try:
        notifier.send_text(f"🔴 Ajar stopped. Code {code}. See RUNBOOK.md.")
    except UpstreamError as exc:
        log(logging.ERROR, "crash_alert_failed", error_code=exc.code)


if __name__ == "__main__":
    sys.exit(main())
