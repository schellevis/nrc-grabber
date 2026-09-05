"""Date helpers: candidate generation and Saturday classification."""

from __future__ import annotations

import datetime as _dt
import sys


def effective_timezone(tz: str) -> tuple[_dt.tzinfo, bool]:
    """Return (tzinfo, used_fallback) for `tz`.

    Falls back to a fixed, documented zone (UTC) if `ZoneInfo(tz)` cannot be
    loaded (missing tzdata or an invalid zone name), so callers can agree on
    one effective timezone even in a degraded environment.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz), False
    except Exception:
        return _dt.timezone.utc, True


def today_local(tz: str = "Europe/Amsterdam") -> _dt.date:
    """Return the current date in the given timezone (UTC on fallback).

    Warns on stderr when falling back, mirroring the scheduler's own
    warning, so a one-shot (RUN_ONCE) run doesn't silently use the wrong
    timezone the way a bare `effective_timezone()` call would.
    """
    zone, used_fallback = effective_timezone(tz)
    if used_fallback:
        print(
            f"nrc-grabber: warning: could not load timezone {tz!r}; "
            "falling back to UTC",
            file=sys.stderr,
        )
    return _dt.datetime.now(zone).date()


def resolve_local_instant(
    zone: _dt.tzinfo, year: int, month: int, day: int, hour: int, minute: int
) -> _dt.datetime:
    """Resolve a local wall-clock time in `zone` to a concrete aware UTC instant.

    - A normal, unambiguous, existing local time converts directly.
    - An ambiguous local time (autumn "fall back" fold) is resolved
      deterministically to fold=0, the first (earlier-UTC-offset)
      occurrence: never skipped, never run twice.
    - A nonexistent local time (spring "spring forward" gap) resolves to the
      first valid concrete instant after the gap (the DST transition
      instant itself), not to the wall-clock time shifted by the length of
      the gap.
    """
    candidate = _dt.datetime(year, month, day, hour, minute, tzinfo=zone, fold=0)
    candidate_utc = candidate.astimezone(_dt.timezone.utc)
    roundtrip = candidate_utc.astimezone(zone)
    target = (year, month, day, hour, minute)
    if (roundtrip.year, roundtrip.month, roundtrip.day, roundtrip.hour, roundtrip.minute) == target:
        return candidate_utc  # exists (possibly ambiguous; fold=0 is already deterministic)

    # Nonexistent: bisect the UTC timeline for the exact DST transition
    # instant. fold=0 and fold=1 give UTC instants using the pre- and
    # post-transition UTC offsets respectively; the transition instant lies
    # between them, and its post-transition local reading is the first
    # valid concrete instant after the gap. ZoneInfo transitions land on a
    # whole UTC second, so bisecting on integral POSIX seconds (rather than
    # halving a timedelta, which accumulates fractional-second remainders
    # and never actually reaches the boundary) converges exactly on it.
    fold1_utc = _dt.datetime(year, month, day, hour, minute, tzinfo=zone, fold=1).astimezone(_dt.timezone.utc)
    lo_ts, hi_ts = sorted((int(candidate_utc.timestamp()), int(fold1_utc.timestamp())))
    pre_offset = _dt.datetime.fromtimestamp(lo_ts, _dt.timezone.utc).astimezone(zone).utcoffset()
    while hi_ts - lo_ts > 1:
        mid_ts = (lo_ts + hi_ts) // 2
        mid = _dt.datetime.fromtimestamp(mid_ts, _dt.timezone.utc)
        if mid.astimezone(zone).utcoffset() == pre_offset:
            lo_ts = mid_ts
        else:
            hi_ts = mid_ts
    return _dt.datetime.fromtimestamp(hi_ts, _dt.timezone.utc)


def expected_edition_date(today: _dt.date) -> _dt.date | None:
    """Return the edition expected to be published for `today`, or None.

    Tue-Sat -> that calendar day; Sunday -> the preceding Saturday (the
    edition NRC actually serves); Monday -> None (no edition is published).
    """
    weekday = today.weekday()  # Monday=0 ... Sunday=6
    if weekday == 0:
        return None
    if weekday == 6:
        return today - _dt.timedelta(days=1)
    return today


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
