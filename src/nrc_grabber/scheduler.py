"""In-container daily scheduler: run at a configured local time, retry on
failure/expected-edition-not-obtained, skip configured weekdays.

Fully driven by injected `now_fn`/`sleep_fn`/`run_fn` so it can be unit
tested with a fake clock and no real sleeping or network access. All elapsed
waits are computed from timezone-aware instants (never naive local
subtraction) so DST transitions do not produce a wrong sleep duration.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import config as _config
from . import dates as _dates

MAX_ADVANCE_DAYS = 8  # SKIP_WEEKDAYS can name at most 6 days (all 7 is rejected by config)


class _Shutdown(BaseException):
    """Raised by the installed SIGTERM/SIGINT handler to unwind cleanly."""


class _FailedOutcome:
    """Duck-typed stand-in for RunOutcome when a run raises unexpectedly."""

    exit_code = 1
    downloaded = 0
    edition_expected = False
    edition_obtained = False


def _default_now_fn() -> datetime:
    return datetime.now(timezone.utc)


def _default_run_fn(cfg: _config.Config):
    from . import _execute

    return _execute(cfg)


def _effective_zone(cfg: _config.Config):
    zone, used_fallback = _dates.effective_timezone(cfg.tz)
    if used_fallback:
        print(
            f"scheduler: warning: could not load timezone {cfg.tz!r}; "
            "falling back to UTC for scheduling",
            file=sys.stderr,
        )
    return zone


def _next_scheduled_instant(cfg: _config.Config, zone, after: datetime) -> datetime:
    """Return the next daily RUN_AT instant (aware UTC) strictly after `after`.

    Skips SKIP_WEEKDAYS entirely. A configured RUN_AT that is ambiguous or
    nonexistent on a DST-transition day is resolved deterministically (always
    fold=0) to a single concrete instant: never skipped, never run twice.
    """
    hour, minute = _config.parse_run_at(cfg.run_at)
    day = after.astimezone(zone).date()
    for _ in range(MAX_ADVANCE_DAYS):
        candidate_utc = _dates.resolve_local_instant(zone, day.year, day.month, day.day, hour, minute)
        if day.weekday() not in cfg.skip_weekdays and candidate_utc > after:
            return candidate_utc
        day = day + timedelta(days=1)
    raise RuntimeError("scheduler: could not find a future run day (check SKIP_WEEKDAYS)")


def _sleep_until(target_utc: datetime, now_fn: Callable[[], datetime], sleep_fn: Callable[[float], None]) -> None:
    now = now_fn()
    remaining = (target_utc - now).total_seconds()
    if remaining > 0:
        sleep_fn(remaining)


def _run_once_safely(cfg: _config.Config, run_fn):
    """Call run_fn(cfg); normalize an ordinary unexpected exception to a
    retryable failed outcome. Shutdown signals are not caught here."""
    try:
        outcome = run_fn(cfg)
    except _Shutdown:
        raise
    except Exception as e:  # noqa: BLE001 - deliberate: any ordinary failure is retryable
        print(f"scheduler: run raised an unexpected error: {e}", file=sys.stderr)
        outcome = _FailedOutcome()
    needs_retry = (outcome.exit_code != 0) or (outcome.edition_expected and not outcome.edition_obtained)
    return outcome, needs_retry


def _run_retries(cfg, zone, scheduled_instant, next_scheduled_instant, now_fn, sleep_fn, run_fn):
    """Run up to RETRY_ATTEMPTS retries, spaced RETRY_DELAY_MINUTES apart on
    UTC instants anchored to `scheduled_instant`. Stops early on success.

    RETRY_DELAY_MINUTES=0 means "retry immediately": each retry runs right
    after the previous attempt completes (bounded by RETRY_ATTEMPTS) instead
    of being anchored to a fixed instant, since anchoring a zero-delay retry
    to `scheduled_instant` would make it equal to `scheduled_instant` itself
    and thus already-passed as soon as any real time elapses. Positive
    delays keep the anchored stale-slot skipping (a retry slot that a slow
    run has already run past is skipped, not burst-fired late).
    """
    immediate = cfg.retry_delay_minutes == 0
    for k in range(1, cfg.retry_attempts + 1):
        now = now_fn()
        retry_instant = now if immediate else scheduled_instant + timedelta(minutes=cfg.retry_delay_minutes * k)
        if retry_instant >= next_scheduled_instant:
            print(f"scheduler: retry {k} would land on/after the next scheduled run; not scheduling")
            break
        retry_day = retry_instant.astimezone(zone).date()
        if retry_day.weekday() in cfg.skip_weekdays:
            print(f"scheduler: retry {k} falls on a skipped weekday; not scheduling")
            continue
        if not immediate and retry_instant < now:
            print(f"scheduler: retry {k} instant already passed; not firing late")
            continue
        print(f"scheduler: retry {k}/{cfg.retry_attempts} at {retry_instant.astimezone(zone).isoformat()}")
        _sleep_until(retry_instant, now_fn, sleep_fn)
        _outcome, needs_retry = _run_once_safely(cfg, run_fn)
        if not needs_retry:
            print("scheduler: retry succeeded; no further retries today")
            break


def run_scheduler(
    cfg: _config.Config,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    run_fn: Optional[Callable[[_config.Config], object]] = None,
    max_cycles: Optional[int] = None,
) -> int:
    """Run the daily scheduler loop.

    `max_cycles` bounds the number of complete daily groups (the scheduled run
    plus any retries) for testing; None (the production default) runs forever
    until a shutdown signal is received. Returns 0 on a clean shutdown signal
    or after `max_cycles` groups; a pre-loop scheduling error returns 1.
    """
    now_fn = now_fn or _default_now_fn
    sleep_fn = sleep_fn or time.sleep
    run_fn = run_fn or _default_run_fn
    zone = _effective_zone(cfg)

    def _handler(signum, frame):
        raise _Shutdown()

    old_term = signal.signal(signal.SIGTERM, _handler)
    old_int = signal.signal(signal.SIGINT, _handler)
    try:
        try:
            scheduled = _next_scheduled_instant(cfg, zone, now_fn())
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1

        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            print(f"scheduler: next run at {scheduled.astimezone(zone).isoformat()}")
            try:
                _sleep_until(scheduled, now_fn, sleep_fn)
                print("scheduler: run starting")
                _outcome, needs_retry = _run_once_safely(cfg, run_fn)
                # Nominal next scheduled boundary, used only as today's retry
                # cutoff (the anchor retries are spaced from/must land before).
                retry_cutoff = _next_scheduled_instant(cfg, zone, scheduled)
                if needs_retry:
                    if cfg.retry_attempts > 0:
                        _run_retries(cfg, zone, scheduled, retry_cutoff, now_fn, sleep_fn, run_fn)
                    else:
                        print("scheduler: run needs retry but RETRY_ATTEMPTS=0; skipping")
                # The daily group (run + retries) may have taken far longer than
                # one day (e.g. a long-hung request). Pick the next daily run
                # strictly after the *current* clock, not merely after the
                # already-consumed `scheduled` instant, so an overrun can't
                # burst-execute multiple stale past daily groups back to back.
                scheduled = _next_scheduled_instant(cfg, zone, max(scheduled, now_fn()))
            except _Shutdown:
                print("scheduler: shutdown signal received; exiting")
                return 0
            cycles += 1
        return 0
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
