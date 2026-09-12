"""Scheduler tests: fake clock, no real sleeping, no network."""

import os
import signal
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from unittest.mock import patch

from nrc_grabber import scheduler
from nrc_grabber.config import Config


def make_cfg(**overrides) -> Config:
    base = dict(
        username="u",
        password="p",
        fmt="pdf",
        output_dir="/downloads",
        keep_saturday=8,
        keep_weekday=14,
        lookback_days=0,
        tz="Europe/Amsterdam",
        run_once=False,
        run_at="06:00",
        retry_delay_minutes=120,
        retry_attempts=1,
        skip_weekdays=frozenset({6}),
    )
    base.update(overrides)
    return Config(**base)


class FakeOutcome(NamedTuple):
    exit_code: int
    downloaded: int
    edition_expected: bool
    edition_obtained: bool


SUCCESS = FakeOutcome(0, 1, True, True)
FAILURE = FakeOutcome(1, 0, False, False)
NO_EDITION_EXPECTED_CLEAN = FakeOutcome(0, 0, False, False)
EDITION_EXPECTED_NOT_OBTAINED = FakeOutcome(0, 0, True, False)


class FakeClock:
    """A mutable instant used as both now_fn and to drive sleep_fn."""

    def __init__(self, start: datetime):
        self.now = start
        self.sleep_calls = []

    def now_fn(self) -> datetime:
        return self.now

    def sleep_fn(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


class RunFnQueue:
    """A run_fn stub that returns canned outcomes in order, recording calls."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, cfg):
        self.calls += 1
        if not self.outcomes:
            return SUCCESS
        return self.outcomes.pop(0)


class NextScheduledInstantTests(unittest.TestCase):
    def test_strictly_future_same_day_before_run_at(self):
        cfg = make_cfg(run_at="06:00", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        after = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)  # Tue, before 06:00 local
        result = scheduler._next_scheduled_instant(cfg, zone, after)
        local = result.astimezone(zone)
        self.assertEqual((local.year, local.month, local.day, local.hour, local.minute), (2026, 9, 8, 6, 0))

    def test_moves_to_tomorrow_when_today_already_passed(self):
        cfg = make_cfg(run_at="06:00", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        after = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)  # well past 06:00 local
        result = scheduler._next_scheduled_instant(cfg, zone, after)
        local = result.astimezone(zone)
        self.assertEqual(local.date().day, 9)

    def test_skips_sunday_default(self):
        cfg = make_cfg(run_at="06:00")  # default skip_weekdays = {6} (Sunday)
        zone = scheduler._effective_zone(cfg)
        # Saturday 2026-09-05 just after run time -> next should be Monday? No,
        # Monday isn't skipped by default; but NRC has no Monday edition, that's
        # a runner concern, not a scheduler one. Only Sunday is skipped here.
        after = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
        result = scheduler._next_scheduled_instant(cfg, zone, after)
        local = result.astimezone(zone)
        self.assertEqual(local.weekday(), 0)  # Monday, Sunday skipped
        self.assertEqual(local.day, 7)

    def test_all_seven_skip_would_be_rejected_at_config_load(self):
        # scheduler itself just trusts cfg.skip_weekdays; config.load() is what
        # rejects all-seven (see tests/test_config.py). Confirm the scheduler
        # raises rather than looping forever if it ever received such a set.
        cfg = make_cfg(skip_weekdays=frozenset(range(7)))
        zone = scheduler._effective_zone(cfg)
        with self.assertRaises(RuntimeError):
            scheduler._next_scheduled_instant(cfg, zone, datetime(2026, 9, 8, tzinfo=timezone.utc))


class DstTests(unittest.TestCase):
    def test_saturday_to_sunday_wait_is_23_hours_across_spring_transition(self):
        # Europe/Amsterdam springs forward on the night of 2026-03-29
        # (clocks 02:00 -> 03:00 CET->CEST, Saturday RUN_AT -> Sunday RUN_AT
        # with no weekday skipped): 23h elapse, not 24h.
        cfg = make_cfg(run_at="06:00", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        first = datetime(2026, 3, 27, 5, 0, tzinfo=timezone.utc)  # before Sat 06:00 local (CET, UTC+1)
        sched1 = scheduler._next_scheduled_instant(cfg, zone, first)
        sched2 = scheduler._next_scheduled_instant(cfg, zone, sched1)
        elapsed = (sched2 - sched1).total_seconds() / 3600.0
        self.assertAlmostEqual(elapsed, 23.0, places=6)

    def test_saturday_to_monday_wait_is_47_hours_via_sleep_until(self):
        # Saturday RUN_AT -> Monday RUN_AT (Sunday skipped, the default) spans
        # two calendar days minus the one hour lost to the spring-forward
        # transition: 47 hours, not 48. Exercise the actual wait primitive
        # (_sleep_until), not just instant subtraction, with a recording fake
        # sleep, as required by the acceptance matrix.
        cfg = make_cfg(run_at="06:00")  # default skip_weekdays={6} (Sunday)
        zone = scheduler._effective_zone(cfg)
        before_sat = datetime(2026, 3, 27, 5, 0, tzinfo=timezone.utc)  # Fri 06:00 CET, before Sat
        saturday = scheduler._next_scheduled_instant(cfg, zone, before_sat)
        monday = scheduler._next_scheduled_instant(cfg, zone, saturday)
        self.assertEqual(monday.astimezone(zone).weekday(), 0)  # Monday

        recorded = []

        def fake_sleep(seconds):
            recorded.append(seconds)

        scheduler._sleep_until(monday, lambda: saturday, fake_sleep)
        self.assertEqual(len(recorded), 1)
        self.assertAlmostEqual(recorded[0] / 3600.0, 47.0, places=6)

    def test_missing_zone_falls_back_gracefully_and_logs(self):
        cfg = make_cfg(tz="Definitely/NotAZone")
        with patch("nrc_grabber.scheduler.sys.stderr") as mock_stderr:
            zone = scheduler._effective_zone(cfg)
        self.assertEqual(zone, timezone.utc)
        self.assertTrue(mock_stderr.write.called)

    def test_ambiguous_and_nonexistent_run_at_resolve_to_one_instant(self):
        cfg = make_cfg(run_at="02:30", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        # Nonexistent: 2026-03-29 02:30 local doesn't exist (spring gap).
        # The first valid concrete instant after the gap is 03:00, not 03:30
        # (which would merely be the target shifted forward by the length of
        # the gap).
        gap_day_before = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
        first = scheduler._next_scheduled_instant(cfg, zone, gap_day_before)
        self.assertEqual(first.astimezone(zone).date().day, 28)
        second = scheduler._next_scheduled_instant(cfg, zone, first)
        self.assertEqual(second, datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc))
        local_second = second.astimezone(zone)
        self.assertEqual((local_second.day, local_second.hour, local_second.minute), (29, 3, 0))
        third = scheduler._next_scheduled_instant(cfg, zone, second)
        self.assertEqual(third.astimezone(zone).date().day, 30)
        # No duplicate/skip: each is strictly after the previous.
        self.assertLess(first, second)
        self.assertLess(second, third)

        # Ambiguous: 2026-10-25 02:30 local occurs twice (fall back);
        # resolved deterministically to the first (fold=0) occurrence.
        before_fold = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
        d1 = scheduler._next_scheduled_instant(cfg, zone, before_fold)
        d2 = scheduler._next_scheduled_instant(cfg, zone, d1)
        self.assertEqual(d1.astimezone(zone).date().day, 24)
        self.assertEqual(d2.astimezone(zone).date().day, 25)
        local_d2 = d2.astimezone(zone)
        self.assertEqual((local_d2.hour, local_d2.minute), (2, 30))
        self.assertLess(d1, d2)

    def test_nonmidpoint_gap_run_at_strict_future_just_after_transition_advances_to_next_day(self):
        # RUN_AT=02:01 also falls inside the spring gap (not the lucky
        # midpoint 02:30). The resolved instant must be exactly the
        # transition boundary (2026-03-29T01:00:00Z); asking for strictly
        # future just after that boundary must advance to March 30, not
        # return a fabricated later fraction of the same March 29 boundary.
        cfg = make_cfg(run_at="02:01", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        transition = datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc)
        before = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
        first = scheduler._next_scheduled_instant(cfg, zone, before)
        self.assertEqual(first.astimezone(zone).date().day, 28)
        second = scheduler._next_scheduled_instant(cfg, zone, first)
        self.assertEqual(second, transition)
        self.assertEqual(second.microsecond, 0)

        just_after = datetime(2026, 3, 29, 1, 0, 0, 100000, tzinfo=timezone.utc)  # +0.1s past transition
        nxt = scheduler._next_scheduled_instant(cfg, zone, just_after)
        self.assertEqual(nxt.astimezone(zone).date(), datetime(2026, 3, 30).date())
        self.assertGreater(nxt, just_after)

    def test_full_loop_runs_exactly_once_per_day_across_spring_transition(self):
        # A RUN_AT that falls inside the spring gap must still produce
        # exactly one run per calendar day across the transition, using the
        # resolved concrete instant, when driven through the full scheduler
        # loop (not just the instant calculator in isolation).
        # The first run is the immediate startup catch-up (today, Mar 28);
        # only the dates are asserted here, so it lands in the same slot the
        # old scheduled-only assertion expected.
        cfg = make_cfg(run_at="02:30", skip_weekdays=frozenset())
        zone = scheduler._effective_zone(cfg)
        clock = FakeClock(datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc))
        starts = []

        def run_fn(cfg):
            starts.append(clock.now.astimezone(zone).date())
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=3
        )
        self.assertEqual(code, 0)
        self.assertEqual(starts, [
            datetime(2026, 3, 28).date(),
            datetime(2026, 3, 29).date(),
            datetime(2026, 3, 30).date(),
        ])

    def test_full_loop_runs_exactly_once_per_day_across_autumn_transition(self):
        # 2026-10-25 02:30 Amsterdam is ambiguous (occurs twice, fall back at
        # 03:00 CEST -> 02:00 CET). Driven through the full scheduler loop
        # (Sunday enabled, not skipped), the ambiguous day must still produce
        # exactly one run -- not two (once for each fold) -- and the exact
        # UTC start instants must reflect the CEST->CET offset change.
        #
        # The first entry is the immediate startup catch-up run (fired at the
        # real "now", not at a RUN_AT boundary); today's own RUN_AT slot
        # (Oct 24 00:30 UTC) is then skipped since the catch-up already
        # covers Oct 24, so the schedule resumes at Oct 25.
        cfg = make_cfg(run_at="02:30", skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc))  # before Oct 24 00:30 UTC
        starts = []

        def run_fn(cfg):
            starts.append(clock.now)
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=3
        )
        self.assertEqual(code, 0)
        self.assertEqual(starts, [
            datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 26, 1, 30, tzinfo=timezone.utc),
        ])


class RetryDecisionTests(unittest.TestCase):
    def test_success_no_retry(self):
        _outcome, needs_retry = scheduler._run_once_safely(make_cfg(), lambda cfg: SUCCESS)
        self.assertFalse(needs_retry)

    def test_failure_needs_retry(self):
        _outcome, needs_retry = scheduler._run_once_safely(make_cfg(), lambda cfg: FAILURE)
        self.assertTrue(needs_retry)

    def test_expected_not_obtained_needs_retry(self):
        _outcome, needs_retry = scheduler._run_once_safely(make_cfg(), lambda cfg: EDITION_EXPECTED_NOT_OBTAINED)
        self.assertTrue(needs_retry)

    def test_monday_clean_absence_no_retry(self):
        # exit 0, edition not expected (Monday) -> not a retry trigger
        _outcome, needs_retry = scheduler._run_once_safely(make_cfg(), lambda cfg: NO_EDITION_EXPECTED_CLEAN)
        self.assertFalse(needs_retry)

    def test_ordinary_exception_normalizes_to_retryable_failure(self):
        def boom(cfg):
            raise OSError("transient")

        outcome, needs_retry = scheduler._run_once_safely(make_cfg(), boom)
        self.assertTrue(needs_retry)
        self.assertEqual(outcome.exit_code, 1)

    def test_shutdown_is_not_normalized(self):
        def boom(cfg):
            raise scheduler._Shutdown()

        with self.assertRaises(scheduler._Shutdown):
            scheduler._run_once_safely(make_cfg(), boom)


class RunSchedulerLoopTests(unittest.TestCase):
    def _run(self, cfg, outcomes, max_cycles, start=datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)):
        clock = FakeClock(start)
        run_fn = RunFnQueue(outcomes)
        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=max_cycles
        )
        return code, clock, run_fn

    def test_max_cycles_zero_runs_nothing(self):
        cfg = make_cfg()
        code, clock, run_fn = self._run(cfg, [], max_cycles=0)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 0)

    def test_success_no_retry_one_cycle(self):
        cfg = make_cfg(skip_weekdays=frozenset())
        code, clock, run_fn = self._run(cfg, [SUCCESS], max_cycles=1)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 1)

    def test_failure_triggers_retry_then_stops_after_success(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=2, retry_delay_minutes=60)
        code, clock, run_fn = self._run(cfg, [FAILURE, SUCCESS], max_cycles=1)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 2)  # initial failure + one successful retry, then stop

    def test_zero_attempts_disables_retry(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=0)
        code, clock, run_fn = self._run(cfg, [FAILURE], max_cycles=1)
        self.assertEqual(run_fn.calls, 1)

    def test_zero_delay_retry_fires_immediately(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=0)
        code, clock, run_fn = self._run(cfg, [FAILURE, SUCCESS], max_cycles=1)
        self.assertEqual(run_fn.calls, 2)

    def test_zero_delay_retry_fires_despite_elapsed_run_time(self):
        # A zero-delay retry anchored to the scheduled instant would equal
        # that instant exactly and so be rejected as "already passed" the
        # moment any real processing time elapses during the initial run.
        # RETRY_DELAY_MINUTES=0 must still fire the retry immediately after
        # completion regardless of how long the run itself took (B1).
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=0)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def run_fn(cfg):
            calls.append(1)
            if len(calls) == 1:
                clock.sleep_fn(1)  # simulate 1 second of real processing time
                return FAILURE
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)

    def test_multiple_zero_delay_retries_all_fire_until_exhausted(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=3, retry_delay_minutes=0)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def run_fn(cfg):
            calls.append(1)
            clock.sleep_fn(1)
            return FAILURE

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4)  # initial + 3 exhausted retries

    def test_daily_overrun_does_not_burst_through_stale_daily_groups(self):
        # A run that overruns by more than a day must not cause the loop to
        # compute a next daily instant that is already in the past relative
        # to the current clock: that produces a tight burst of back-to-back
        # runs for stale days instead of one run per (skipped-ahead) day (B2).
        cfg = make_cfg(skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        starts = []

        def run_fn(cfg):
            starts.append(clock.now)
            if len(starts) == 1:
                clock.sleep_fn(49 * 3600)  # the first run itself overruns by 49h
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=3
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(starts), 3)
        for i in range(1, len(starts)):
            gap_hours = (starts[i] - starts[i - 1]).total_seconds() / 3600.0
            self.assertGreaterEqual(
                gap_hours, 23.0,
                f"stale burst detected: run {i} started only {gap_hours}h after run {i - 1}",
            )

    def test_retry_anchored_to_schedule_leaves_correct_future_slot(self):
        # The run itself takes real (simulated) time, but the retry is
        # spaced from the *scheduled* instant, not from when the run
        # happened to finish, so it lands at the previously announced slot.
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=30)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def run_fn(cfg):
            calls.append(clock.now)
            if len(calls) == 1:
                clock.sleep_fn(5 * 60)  # the run itself takes 5 simulated minutes
                return FAILURE
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual((calls[1] - calls[0]).total_seconds(), 30 * 60, places=6)

    def test_multiple_retries_fire_within_one_group(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=2, retry_delay_minutes=30)
        code, clock, run_fn = self._run(cfg, [FAILURE, FAILURE, SUCCESS], max_cycles=1)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 3)  # initial failure + retry 1 (failure) + retry 2 (success)

    def test_loop_level_ordinary_exception_retries_and_continues(self):
        # Exercise the exception-normalization/retry boundary through the
        # real run_scheduler loop, not just _run_once_safely in isolation.
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=60)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def run_fn(cfg):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("transient")
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)

    def test_loop_level_ordinary_exception_exhausts_retries_then_next_day_runs(self):
        # Exercise the full daily group under repeated ordinary (non-Shutdown)
        # exceptions -- not a canned FAILURE outcome -- so every attempt in
        # the first group (the initial run plus all retries) raises OSError,
        # RETRY_ATTEMPTS is exhausted, and the loop still proceeds to the
        # next day's run rather than stopping or crashing.
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=2, retry_delay_minutes=60)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def run_fn(cfg):
            calls.append(1)
            if len(calls) <= 3:  # initial + 2 retries, all raise
                raise OSError("transient")
            return SUCCESS  # next day's run

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=2
        )
        self.assertEqual(code, 0)
        # cycle 1: initial OSError + 2 exhausted OSError retries = 3 calls
        # cycle 2: next day's run succeeds = 1 call
        self.assertEqual(len(calls), 4)

    def test_retry_equal_to_next_scheduled_instant_is_not_scheduled(self):
        # RUN_AT is 06:00 daily -> next scheduled is 24h later; a 24h (1440min)
        # delay for the single retry lands exactly on it -> not scheduled.
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=1440)
        code, clock, run_fn = self._run(cfg, [FAILURE], max_cycles=1)
        self.assertEqual(run_fn.calls, 1)

    def test_retry_overrunning_a_slot_is_skipped_not_burst_fired(self):
        # Two retries configured 30 min apart; the run itself (simulated via
        # sleep_fn advancing the clock inside the fake run) burns 90 minutes,
        # so by the time we'd consider retry 1 (at +30min) it's already passed;
        # retry 2 (at +60min) has also passed. Neither fires as a late burst.
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=2, retry_delay_minutes=30)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

        def slow_failure(cfg):
            clock.sleep_fn(90 * 60)  # the run itself takes 90 simulated minutes
            return FAILURE

        run_fn = RunFnQueue([])
        run_fn.outcomes = None  # unused; we drive via slow_failure directly
        calls = []

        def wrapped(cfg):
            calls.append(1)
            if len(calls) == 1:
                return slow_failure(cfg)
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=wrapped, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)  # neither stale retry fired

    def test_retry_on_skipped_weekday_is_not_scheduled(self):
        # Saturday run fails; RETRY_DELAY_MINUTES pushes the single retry into
        # Sunday, which is skipped by default -> not scheduled, loop continues.
        cfg = make_cfg(retry_attempts=1, retry_delay_minutes=20 * 60)  # +20h -> Sunday
        start = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)  # before Sat 06:00 Amsterdam
        code, clock, run_fn = self._run(cfg, [FAILURE], max_cycles=1, start=start)
        self.assertEqual(run_fn.calls, 1)

    def test_loop_continues_after_retries_exhausted_still_failing(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=60)
        code, clock, run_fn = self._run(cfg, [FAILURE, FAILURE, SUCCESS], max_cycles=2)
        self.assertEqual(code, 0)
        # cycle 1: initial failure + 1 retry (also failure, exhausted) = 2 calls
        # cycle 2: initial success = 1 call; loop did not crash/stop after exhaustion
        self.assertEqual(run_fn.calls, 3)

    def test_max_cycles_none_runs_until_shutdown_signal(self):
        cfg = make_cfg(skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def sleep_fn(seconds):
            calls.append(seconds)
            if len(calls) > 3:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            clock.sleep_fn(seconds)

        run_fn = RunFnQueue([SUCCESS, SUCCESS, SUCCESS, SUCCESS, SUCCESS])
        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=sleep_fn, run_fn=run_fn, max_cycles=None
        )
        self.assertEqual(code, 0)
        # The immediate startup catch-up run (no sleep) plus 3 scheduled
        # cycles ran before the signal landed on the 4th sleep_fn call.
        self.assertEqual(run_fn.calls, 4)

    def test_max_cycles_counts_daily_groups_including_retries(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=60)
        code, clock, run_fn = self._run(cfg, [FAILURE, SUCCESS, SUCCESS], max_cycles=2)
        # cycle 1: initial failure + 1 retry (success) = 2 calls
        # cycle 2: initial success = 1 call
        self.assertEqual(run_fn.calls, 3)


class StartupCatchupTests(unittest.TestCase):
    """The scheduler runs once immediately on startup (a catch-up pass) so a
    freshly (re)started daemon doesn't sit idle until the next RUN_AT."""

    def test_runs_immediately_with_no_wait(self):
        cfg = make_cfg(skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))  # well before 06:00 local
        run_fn = RunFnQueue([SUCCESS])
        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 1)
        self.assertEqual(clock.sleep_calls, [])  # no wait before the startup run

    def test_skipped_on_a_skip_weekday(self):
        # Default skip_weekdays={6} (Sunday): starting up on a Sunday must
        # not trigger the catch-up run; the scheduler waits for the next
        # valid RUN_AT instead.
        cfg = make_cfg()  # default skip_weekdays={6}
        clock = FakeClock(datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc))  # Sunday
        run_fn = RunFnQueue([SUCCESS])
        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=1
        )
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 1)
        self.assertEqual(len(clock.sleep_calls), 1)  # genuinely waited for the next RUN_AT
        self.assertEqual(clock.now.date().weekday(), 0)  # landed on Monday, not Sunday

    def test_todays_still_upcoming_run_at_is_not_also_run(self):
        # Starting up before today's RUN_AT: the catch-up run covers today,
        # so today's own RUN_AT slot is skipped -- no double run on day one.
        cfg = make_cfg(run_at="06:00", skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))  # 02:00 local, before 06:00
        starts = []

        def run_fn(cfg):
            starts.append(clock.now.date())
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=2
        )
        self.assertEqual(code, 0)
        self.assertEqual(starts, [datetime(2026, 9, 8).date(), datetime(2026, 9, 9).date()])

    def test_todays_already_passed_run_at_is_not_run_again(self):
        # Starting up after today's RUN_AT already passed: the catch-up run
        # still covers today, and the following scheduled run is tomorrow --
        # same outcome as above, arrived at without needing to skip a slot.
        cfg = make_cfg(run_at="06:00", skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))  # well after 06:00 local
        starts = []

        def run_fn(cfg):
            starts.append(clock.now.date())
            return SUCCESS

        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=2
        )
        self.assertEqual(code, 0)
        self.assertEqual(starts, [datetime(2026, 9, 8).date(), datetime(2026, 9, 9).date()])

    def test_failed_startup_run_is_retried_like_any_scheduled_run(self):
        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=30)
        code, clock, run_fn = self._run(cfg, [FAILURE, SUCCESS], max_cycles=1)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 2)  # initial startup failure + one successful retry
        self.assertEqual(len(clock.sleep_calls), 1)  # just the retry-wait; no wait before the startup run

    @staticmethod
    def _run(cfg, outcomes, max_cycles, start=datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)):
        clock = FakeClock(start)
        run_fn = RunFnQueue(outcomes)
        code = scheduler.run_scheduler(
            cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=run_fn, max_cycles=max_cycles
        )
        return code, clock, run_fn

    def test_max_cycles_zero_skips_even_the_startup_run(self):
        cfg = make_cfg(skip_weekdays=frozenset())
        code, clock, run_fn = self._run(cfg, [], max_cycles=0)
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 0)


class SignalHandlingTests(unittest.TestCase):
    def test_sigterm_during_wait_for_next_run_exits_cleanly(self):
        # The immediate startup catch-up run fires with no preceding sleep,
        # so the first sleep_fn call is the wait for the *next* scheduled
        # run; that's what this signal interrupts.
        import nrc_grabber as app

        cfg = make_cfg(skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

        def sleep_and_signal(seconds):
            os.kill(os.getpid(), signal.SIGTERM)

        run_fn = RunFnQueue([SUCCESS, SUCCESS])
        with patch.object(app._prune, 'prune') as mock_prune:
            code = scheduler.run_scheduler(
                cfg, now_fn=clock.now_fn, sleep_fn=sleep_and_signal, run_fn=run_fn, max_cycles=5
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 1)  # only the startup catch-up run
        mock_prune.assert_not_called()

    def test_sigint_during_wait_for_next_run_exits_cleanly(self):
        # See test_sigterm_during_wait_for_next_run_exits_cleanly.
        import nrc_grabber as app

        cfg = make_cfg(skip_weekdays=frozenset())
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

        def sleep_and_signal(seconds):
            os.kill(os.getpid(), signal.SIGINT)

        run_fn = RunFnQueue([SUCCESS, SUCCESS])
        with patch.object(app._prune, 'prune') as mock_prune:
            code = scheduler.run_scheduler(
                cfg, now_fn=clock.now_fn, sleep_fn=sleep_and_signal, run_fn=run_fn, max_cycles=5
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 1)  # only the startup catch-up run
        mock_prune.assert_not_called()

    def test_sigterm_during_startup_run_wait_exits_cleanly_without_running(self):
        # If SKIP_WEEKDAYS names today, the startup catch-up run is itself
        # skipped, so a signal during the (now genuinely initial) wait for
        # the first scheduled run exits cleanly with no run at all.
        import nrc_grabber as app

        cfg = make_cfg(skip_weekdays=frozenset({0}))  # skip Monday
        clock = FakeClock(datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc))  # Monday

        def sleep_and_signal(seconds):
            os.kill(os.getpid(), signal.SIGTERM)

        run_fn = RunFnQueue([SUCCESS])
        with patch.object(app._prune, 'prune') as mock_prune:
            code = scheduler.run_scheduler(
                cfg, now_fn=clock.now_fn, sleep_fn=sleep_and_signal, run_fn=run_fn, max_cycles=5
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_fn.calls, 0)
        mock_prune.assert_not_called()

    def test_sigint_during_retry_wait_exits_cleanly_without_further_retry(self):
        import nrc_grabber as app

        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=10)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def sleep_fn(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                clock.sleep_fn(seconds)
            else:
                os.kill(os.getpid(), signal.SIGINT)

        run_fn = RunFnQueue([FAILURE, SUCCESS])
        with patch.object(app._prune, 'prune') as mock_prune:
            code = scheduler.run_scheduler(
                cfg, now_fn=clock.now_fn, sleep_fn=sleep_fn, run_fn=run_fn, max_cycles=5
            )
        self.assertEqual(code, 0)
        # The immediate startup catch-up run fails, its retry (sleep call #1,
        # real) succeeds, and the wait for the *next scheduled* run is what
        # gets interrupted -- not a second retry.
        self.assertEqual(run_fn.calls, 2)
        # The canned run_fn never touches the real prune module; allowing for
        # any completed clean pass before the interrupted wait, no prune call
        # happens as a result of the interruption itself.
        mock_prune.assert_not_called()

    def test_sigterm_during_retry_wait_exits_cleanly_without_further_retry(self):
        import nrc_grabber as app

        cfg = make_cfg(skip_weekdays=frozenset(), retry_attempts=1, retry_delay_minutes=10)
        clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))
        calls = []

        def sleep_fn(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                clock.sleep_fn(seconds)
            else:
                os.kill(os.getpid(), signal.SIGTERM)

        run_fn = RunFnQueue([FAILURE, SUCCESS])
        with patch.object(app._prune, 'prune') as mock_prune:
            code = scheduler.run_scheduler(
                cfg, now_fn=clock.now_fn, sleep_fn=sleep_fn, run_fn=run_fn, max_cycles=5
            )
        self.assertEqual(code, 0)
        # See test_sigint_during_retry_wait_exits_cleanly_without_further_retry.
        self.assertEqual(run_fn.calls, 2)
        mock_prune.assert_not_called()

    def test_sigint_during_active_run_aborts_without_prune(self):
        # Mirrors test_sigterm_during_active_run_aborts_without_prune with
        # SIGINT, to complete the signal x phase matrix.
        import datetime as dt
        import tempfile

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            from nrc_grabber.config import load

            cfg = load({
                'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                'OUTPUT_DIR': tmp, 'LOOKBACK_DAYS': '0',
                'SKIP_WEEKDAYS': '',
            })
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            def interrupting_download(*args):
                os.kill(os.getpid(), signal.SIGINT)
                raise AssertionError("unreachable: signal should have unwound first")

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest',
                              return_value={'publication_date': '2026-09-08', 'download': {'pdf_full': '/paper'}}), \
                 patch.object(app._nrc, 'download_edition', side_effect=interrupting_download), \
                 patch.object(app._prune, 'prune') as mock_prune:
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=5
                )
            self.assertEqual(code, 0)
            mock_prune.assert_not_called()

    def test_sigterm_during_active_run_aborts_without_prune(self):
        # Drive the real _execute with mocked network so an interruption mid-
        # download demonstrably leaves prune uncalled (BaseException propagates
        # past _execute's narrow except clauses, past run_fn, past the retry
        # wrapper's `except Exception`, to the scheduler's shutdown handler).
        import datetime as dt
        import tempfile

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            from nrc_grabber.config import load

            cfg = load({
                'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                'OUTPUT_DIR': tmp, 'LOOKBACK_DAYS': '0',
                'SKIP_WEEKDAYS': '',
            })
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            def interrupting_download(*args):
                os.kill(os.getpid(), signal.SIGTERM)
                raise AssertionError("unreachable: signal should have unwound first")

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest',
                              return_value={'publication_date': '2026-09-08', 'download': {'pdf_full': '/paper'}}), \
                 patch.object(app._nrc, 'download_edition', side_effect=interrupting_download), \
                 patch.object(app._prune, 'prune') as mock_prune:
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=5
                )
            self.assertEqual(code, 0)
            mock_prune.assert_not_called()


class IntegratedDefaultRunFnTests(unittest.TestCase):
    """Drive the scheduler's DEFAULT run_fn (the real _execute, via
    run_fn=None) rather than a canned FakeOutcome, proving the scheduler's
    retry/no-retry decision at the real outcome/default-wrapper boundary."""

    def _cfg(self, tmp, **overrides):
        from nrc_grabber.config import load

        env = {
            'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
            'OUTPUT_DIR': tmp, 'LOOKBACK_DAYS': '0', 'SKIP_WEEKDAYS': '',
            'RETRY_ATTEMPTS': '1', 'RETRY_DELAY_MINUTES': '60',
        }
        env.update(overrides)
        return load(env)

    @staticmethod
    def _write_download(cj, url, folder, fmt, day):
        from pathlib import Path

        path = Path(folder) / f'NH-{day}.pdf'
        path.write_bytes(b'%PDF-test')
        return path

    def test_successful_download_needs_no_retry(self):
        import datetime as dt
        import tempfile

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest',
                              return_value={'publication_date': '2026-09-08', 'download': {'pdf_full': '/paper'}}), \
                 patch.object(app._nrc, 'download_edition', side_effect=self._write_download):
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=1
                )
            self.assertEqual(code, 0)
            # The single cycle is the immediate startup catch-up run, which
            # doesn't wait before firing; no retry needed, so no sleep at all.
            self.assertEqual(len(clock.sleep_calls), 0)

    def test_older_only_backfill_edition_still_needs_retry(self):
        import datetime as dt
        import tempfile

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, LOOKBACK_DAYS='1')
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            def manifest(cj, day):
                if day == dt.date(2026, 9, 8):
                    return None  # today's edition is unavailable
                return {'publication_date': day.isoformat(), 'download': {'pdf_full': '/paper'}}

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest', side_effect=manifest), \
                 patch.object(app._nrc, 'download_edition', side_effect=self._write_download):
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=1
                )
            self.assertEqual(code, 0)
            # The immediate startup catch-up run doesn't wait before firing,
            # but a retry must still have been scheduled (one retry-wait
            # sleep), since today's expected edition was never obtained even
            # though an older backfill edition downloaded successfully.
            self.assertEqual(len(clock.sleep_calls), 1)

    def test_already_present_edition_needs_no_retry(self):
        import datetime as dt
        import tempfile
        from pathlib import Path

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            (Path(tmp) / 'NH-2026-09-08.pdf').write_bytes(b'%PDF-test')
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest',
                              return_value={'publication_date': '2026-09-08', 'download': {'pdf_full': '/paper'}}), \
                 patch.object(app._nrc, 'download_edition') as mock_download:
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=1
                )
            self.assertEqual(code, 0)
            mock_download.assert_not_called()
            # The single cycle is the immediate startup catch-up run; no wait,
            # no retry.
            self.assertEqual(len(clock.sleep_calls), 0)

    def test_zero_retention_download_then_prune_removes_file_and_needs_no_retry(self):
        # KEEP_WEEKDAY=0 makes the real prune remove the just-downloaded
        # expected edition immediately after the pass. edition_obtained is
        # measured before pruning (see nrc_grabber._execute), so this must
        # still count as obtained: exactly one pass/prune and no retry.
        import datetime as dt
        import tempfile
        from pathlib import Path

        import nrc_grabber as app

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, KEEP_WEEKDAY='0')
            clock = FakeClock(datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc))

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
                 patch.object(app._nrc, 'edition_manifest',
                              return_value={'publication_date': '2026-09-08', 'download': {'pdf_full': '/paper'}}), \
                 patch.object(app._nrc, 'download_edition', side_effect=self._write_download), \
                 patch.object(app._prune, 'prune', wraps=app._prune.prune) as mock_prune:
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=1
                )
            self.assertEqual(code, 0)
            mock_prune.assert_called_once()
            self.assertEqual(list(Path(tmp).iterdir()), [])  # the download was really pruned away
            # The single cycle is the immediate startup catch-up run; no
            # wait, no retry, despite zero-retention pruning immediately
            # removing the file.
            self.assertEqual(len(clock.sleep_calls), 0)

    def test_monday_operational_failure_via_real_execute_triggers_retry(self):
        # Distinct from a clean Monday no-op (no edition expected): a real
        # operational failure on Monday (e.g. a manifest/server error) must
        # still be retried like any other day's failure.
        import datetime as dt
        import tempfile

        import nrc_grabber as app
        from nrc_grabber.nrc import NrcError

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            clock = FakeClock(datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc))  # Monday

            def manifest(cj, day):
                raise NrcError('server error')

            with patch.object(app._nrc, 'login'), \
                 patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 7)), \
                 patch.object(app._nrc, 'edition_manifest', side_effect=manifest):
                code = scheduler.run_scheduler(
                    cfg, now_fn=clock.now_fn, sleep_fn=clock.sleep_fn, run_fn=None, max_cycles=1
                )
            self.assertEqual(code, 0)
            # The immediate startup catch-up run doesn't wait before firing;
            # just the one retry-wait sleep.
            self.assertEqual(len(clock.sleep_calls), 1)


if __name__ == "__main__":
    unittest.main()
