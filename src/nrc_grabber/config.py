"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


class ConfigError(Exception):
    """Raised when configuration is invalid."""


VALID_FORMATS = ("pdf", "epub", "mobi")
FORMAT_KEYS = {"pdf": "pdf_full", "epub": "epub", "mobi": "mobi"}
# Magic bytes: PDF/epub checked at offset 0; mobi is a PalmDB with
# "BOOKMOBI" (type+creator) at offset 60.
MAGIC_PREFIX = {
    "pdf": b"%PDF",
    "epub": b"PK",
}
MAGIC_OFFSET = {
    "mobi": (60, b"BOOKMOBI"),
}


def _nonneg_int_env(env: dict, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a nonnegative integer, got {raw!r}")
    if value < 0:
        raise ConfigError(f"{name} must be nonnegative, got {value}")
    return value


_TRUTHY_TOKENS = {"1", "true", "yes", "on"}
_FALSEY_TOKENS = {"0", "false", "no", "off", ""}


def _bool_env(env: dict, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in _TRUTHY_TOKENS:
        return True
    if token in _FALSEY_TOKENS:
        return False
    raise ConfigError(f"{name} must be one of 1/true/yes/on or 0/false/no/off, got {raw!r}")


def parse_run_at(raw: str) -> tuple[int, int]:
    """Validate and parse an `HH:MM` 24h time-of-day string."""
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ConfigError(f"RUN_AT must be HH:MM, got {raw!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"RUN_AT must be a valid 24h time, got {raw!r}")
    return hour, minute


WEEKDAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_skip_weekdays(raw: str) -> frozenset[int]:
    """Parse a comma-separated list of weekday abbreviations and/or 0-6 integers.

    Empty string means skip nothing. All seven weekdays is rejected: it would
    leave the scheduler with no valid future run day.
    """
    raw = raw.strip()
    if raw == "":
        return frozenset()
    days: set[int] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in WEEKDAY_NAMES:
            days.add(WEEKDAY_NAMES[token])
        elif token.isdigit() and 0 <= int(token) <= 6:
            days.add(int(token))
        else:
            raise ConfigError(f"SKIP_WEEKDAYS: unknown weekday token {token!r}")
    if days == set(range(7)):
        raise ConfigError("SKIP_WEEKDAYS may not name all seven weekdays")
    return frozenset(days)


@dataclass(frozen=True)
class Config:
    username: str
    # Kept out of repr/str so an accidental print or traceback of the config
    # never leaks the subscriber password.
    password: str = field(repr=False)
    fmt: str
    output_dir: str
    keep_saturday: int
    keep_weekday: int
    lookback_days: int
    tz: str
    # Scheduler settings: all defaulted so existing (load()/direct-construction)
    # callers keep working unchanged; run_once() ignores these.
    run_once: bool = False
    run_at: str = "06:00"
    retry_delay_minutes: int = 120
    retry_attempts: int = 1
    skip_weekdays: frozenset[int] = field(default=frozenset({6}))

    @property
    def format_key(self) -> str:
        return FORMAT_KEYS[self.fmt]


def load(environ: Optional[dict] = None) -> Config:
    env = environ if environ is not None else os.environ
    username = env.get("NRC_USERNAME", "")
    password = env.get("NRC_PASSWORD", "")
    if not username or not password:
        raise ConfigError("NRC_USERNAME and NRC_PASSWORD are required")
    fmt = env.get("FORMAT", "pdf").lower()
    if fmt not in VALID_FORMATS:
        raise ConfigError(f"FORMAT must be one of {VALID_FORMATS}, got {fmt!r}")
    keep_saturday = _nonneg_int_env(env, "KEEP_SATURDAY", 8)
    keep_weekday = _nonneg_int_env(env, "KEEP_WEEKDAY", 14)
    lookback = _nonneg_int_env(env, "LOOKBACK_DAYS", 0)
    run_once = _bool_env(env, "RUN_ONCE", False)
    run_at = env.get("RUN_AT", "06:00")
    parse_run_at(run_at)
    retry_delay_minutes = _nonneg_int_env(env, "RETRY_DELAY_MINUTES", 120)
    retry_attempts = _nonneg_int_env(env, "RETRY_ATTEMPTS", 1)
    skip_weekdays = parse_skip_weekdays(env.get("SKIP_WEEKDAYS", "sun"))
    return Config(
        username=username,
        password=password,
        fmt=fmt,
        output_dir=env.get("OUTPUT_DIR", "/downloads"),
        keep_saturday=keep_saturday,
        keep_weekday=keep_weekday,
        lookback_days=lookback,
        tz=env.get("TZ", "Europe/Amsterdam"),
        run_once=run_once,
        run_at=run_at,
        retry_delay_minutes=retry_delay_minutes,
        retry_attempts=retry_attempts,
        skip_weekdays=skip_weekdays,
    )
