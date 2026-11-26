"""Transport layer: turns results into Telegram messages."""

import html
import logging

from .errors import UpstreamRejected
from .logs import log

SOURCE_LABELS = {"remotive": "Remotive", "jobicy": "Jobicy", "himalayas": "Himalayas"}
MAX_LENGTH = 4000


def _clip(text, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_job(row: dict) -> str:
    def safe(value, limit=200):
        return html.escape(_clip(value, limit))

    lines = [f"🚪 <b>{safe(row['title'])}</b>",
             f"🏢 {safe(row['company'])}",
             f"⭐ {row['score']}/100 · {safe(row['track'])}",
             f"🌍 {safe(row.get('location_text') or 'Not stated')}"]
    if row.get("salary"):
        lines.append(f"💰 {safe(row['salary'], 100)}")
    lines += [f"✅ {safe(r)}" for r in row.get("reasons") or []]
    lines += [f"⚠️ {safe(w)}" for w in row.get("warnings") or []]
    source = SOURCE_LABELS.get(row["source"], row["source"])
    lines.append(f'<a href="{html.escape(row["url"])}">View and apply</a> · via {source}')
    return "\n".join(lines)


def plain_job(row: dict) -> str:
    source = SOURCE_LABELS.get(row["source"], row["source"])
    return _clip(f"{row['title']} at {row['company']} ({row['score']}/100)", 500) + \
        f"\n{row['url']}\nvia {source}"


class Telegram:
    def __init__(self, http, token: str, chat_id: str):
        self._http = http
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat_id = chat_id

    def check(self) -> None:
        self._http.request_json("GET", f"{self._base}/getMe", service="telegram")

    def send_text(self, text: str, *, parse_mode: str | None = None) -> None:
        body = {"chat_id": self._chat_id, "text": text[:MAX_LENGTH],
                "disable_web_page_preview": True}
        if parse_mode:
            body["parse_mode"] = parse_mode
        # Not idempotent: retrying after a timeout could deliver the message twice.
        self._http.request_json("POST", f"{self._base}/sendMessage", service="telegram",
                                body=body, idempotent=False)
        self._http.sleep(1.1)  # stay under Telegram's per-chat speed limit

    def send_job(self, row: dict) -> None:
        text = format_job(row)
        if len(text) > MAX_LENGTH:
            self.send_text(plain_job(row))
            return
        try:
            self.send_text(text, parse_mode="HTML")
        except UpstreamRejected:
            log(logging.WARNING, "telegram_format_rejected",
                fingerprint=row["fingerprint"])
            self.send_text(plain_job(row))
