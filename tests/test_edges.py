"""Database client, Telegram messages, config, logging and the command line."""

import json
import logging

import pytest

from ajar import app
from ajar.config import load_secrets, validate_profile
from ajar.errors import ConfigError, UpstreamRejected
from ajar.http import HttpClient
from ajar.logs import JsonFormatter
from ajar.rules import evaluate
from ajar.store import SupabaseStore
from ajar.telegram import Telegram, format_job

from .helpers import NOW, PROFILE, FakeResponse, FakeSession, make_job

ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42",
       "SUPABASE_URL": "https://x.supabase.co/rest/v1/", "SUPABASE_SERVICE_KEY": "sb_secret_z"}
ROW = {"fingerprint": "f1", "source": "jobicy", "title": "Dev <script>", "company": "A&B",
       "url": "https://jobicy.com/jobs/1", "score": 80, "track": "x",
       "reasons": ["ok"], "warnings": []}


def http_with(*script):
    session = FakeSession(*script)
    return HttpClient(session, sleep=lambda s: None), session


def test_store_inserts_only_new_jobs():
    http, session = http_with(FakeResponse(201, [{"fingerprint": "a"}]))
    store = SupabaseStore(http, "https://x.supabase.co", "sb_secret_z")
    assert store.add_matches([evaluate(make_job(), PROFILE, NOW)], NOW) == 1
    method, url, sent = session.calls[0]
    assert (method, url) == ("POST", "https://x.supabase.co/rest/v1/jobs")
    assert "ignore-duplicates" in sent["headers"]["Prefer"]
    assert "Authorization" not in sent["headers"]


def test_old_style_supabase_key_is_also_sent_as_bearer():
    http, session = http_with(FakeResponse(200, []))
    SupabaseStore(http, "https://x.supabase.co", "eyJold").source_state("jobicy")
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer eyJold"


def test_message_escapes_html_and_credits_the_source():
    text = format_job(ROW)
    assert "&lt;script&gt;" in text and "A&amp;B" in text
    assert "via Jobicy" in text


def test_rejected_formatting_falls_back_to_plain_text():
    http, session = http_with(FakeResponse(400), FakeResponse(200, {"ok": True}))
    Telegram(http, "t", "1").send_job(ROW)
    assert "parse_mode" not in session.calls[1][2]["json"]


def test_both_telegram_formats_rejected_raises():
    http, _ = http_with(FakeResponse(400), FakeResponse(400))
    with pytest.raises(UpstreamRejected):
        Telegram(http, "t", "1").send_job(ROW)


def test_missing_secret_is_named_without_showing_values():
    with pytest.raises(ConfigError) as caught:
        load_secrets({**ENV, "TELEGRAM_CHAT_ID": " "})
    assert "TELEGRAM_CHAT_ID" in str(caught.value)
    assert "123:abc" not in str(caught.value)


def test_supabase_url_is_tidied():
    assert load_secrets(ENV).supabase_url == "https://x.supabase.co"


def test_profile_mistake_is_caught():
    broken = {**PROFILE, "tracks": {"a": {"label": "A", "titles": "python", "skills": []}}}
    with pytest.raises(ConfigError):
        validate_profile(broken)


def test_logs_are_json_and_secrets_are_hidden():
    record = logging.LogRecord("ajar", logging.WARNING, __file__, 1, "token 123:abc", (), None)
    entry = json.loads(JsonFormatter(("123:abc",)).format(record))
    assert entry["level"] == "WARN"
    assert entry["event"] == "token [REDACTED]"
    assert entry["run_id"]


def test_pause_switch_does_nothing():
    assert app.main(["run"], env={"AJAR_PAUSED": "true"}) == 0


def test_missing_config_exits_with_code_2():
    assert app.main(["doctor"], env={}) == 2


def test_doctor_checks_everything_even_after_a_failure():
    class Broken:
        def ping(self):
            raise UpstreamRejected("table missing")

    class Fine:
        sent = 0

        def check(self):
            pass

        def send_text(self, text):
            Fine.sent += 1

    assert app.doctor(Broken(), Fine()) == 1
    assert Fine.sent == 1
