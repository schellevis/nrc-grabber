"""Date helpers: candidate generation and Saturday classification."""

from __future__ import annotations

import datetime as _dt


def today_local(tz: str = "Europe/Amsterdam") -> _dt.date:
    """Return the current date in the given timezone."""
    # stdlib only: use datetime.now with timezone via zoneinfo when available,
    # fall back to UTC date otherwise.
    try:
        from zoneinfo import ZoneInfo

        return _dt.datetime.now(ZoneInfo(tz)).date()
    except Exception:
        return _dt.date.today()


def request_dates(today: _dt.date, lookback_days: int) -> list[_dt.date]:
    """Yield candidate request dates: today, then earlier days up to lookback."""
    if lookback_days < 0:
        lookback_days = 0
    return [today - _dt.timedelta(days=i) for i in range(lookback_days + 1)]


def is_saturday(date: _dt.date) -> bool:
    """Return True if the date is a Saturday (NRC weekend edition)."""
    return date.weekday() == 5  # Monday=0, Saturday=5


def edition_date_for_request(request_date: _dt.date, publication_date_str: str | None) -> _dt.date:
    """Resolve the edition date from a manifest publication_date string.

    Falls back to the request date if the string is missing/unparseable.
    """
    if not publication_date_str:
        return request_date
    # Manifest uses RFC-style "Sat, 05 Sep 2026 00:00:00 GMT" via email.utils
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(publication_date_str)
        return dt.date()
    except Exception:
        pass
    # Try ISO
    try:
        return _dt.date.fromisoformat(publication_date_str[:10])
    except Exception:
        return request_date
