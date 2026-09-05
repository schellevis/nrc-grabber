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
    return Config(
        username=username,
        password=password,
        fmt=fmt,
        output_dir=env.get("OUTPUT_DIR", "/downloads"),
        keep_saturday=keep_saturday,
        keep_weekday=keep_weekday,
        lookback_days=lookback,
        tz=env.get("TZ", "Europe/Amsterdam"),
    )
