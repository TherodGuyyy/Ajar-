"""All configuration in one place: secrets from the environment, preferences from profile.json."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

REQUIRED_SECRETS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
)


@dataclass(frozen=True)
class Secrets:
    telegram_token: str
    telegram_chat_id: str
    supabase_url: str
    supabase_key: str

    def sensitive_values(self) -> tuple[str, ...]:
        return (self.telegram_token, self.telegram_chat_id, self.supabase_key)


def load_secrets(env: Mapping[str, str]) -> Secrets:
    """Fail fast, naming (never showing) whatever is missing."""
    values = {name: env.get(name, "").strip() for name in REQUIRED_SECRETS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"Missing environment variables: {', '.join(missing)}",
                          code="CONFIG_MISSING_SECRET")
    url = values["SUPABASE_URL"].rstrip("/").removesuffix("/rest/v1")
    if not url.startswith("https://"):
        raise ConfigError("SUPABASE_URL must start with https://", code="CONFIG_BAD_URL")
    return Secrets(values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_CHAT_ID"], url,
                   values["SUPABASE_SERVICE_KEY"])


def load_profile(path: Path) -> dict:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path.name} was not found") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON (line {exc.lineno})") from exc
    validate_profile(profile)
    return profile


def validate_profile(profile: dict) -> None:
    shape = {
        "min_score": int, "max_age_days": int, "max_alerts_per_run": int,
        "max_years_required": int, "tracks": dict, "excluded_title_words": list,
        "blocking_phrases": list, "himalayas_searches": list,
    }
    for key, kind in shape.items():
        if not isinstance(profile.get(key), kind):
            raise ConfigError(f"profile.json: '{key}' must be of type {kind.__name__}")
    if not 1 <= profile["max_alerts_per_run"] <= 30:
        raise ConfigError("profile.json: max_alerts_per_run must be between 1 and 30")
    for name, track in profile["tracks"].items():
        for key in ("label", "titles", "skills"):
            if key not in track:
                raise ConfigError(f"profile.json: track '{name}' is missing '{key}'")
        for key in ("titles", "skills"):
            # A plain string here would be matched letter by letter.
            if not isinstance(track[key], list):
                raise ConfigError(f"profile.json: track '{name}' '{key}' must be a list")
