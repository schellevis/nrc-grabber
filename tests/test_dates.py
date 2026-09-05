"""Tests for date helpers (no network)."""

import datetime as dt
import unittest
from unittest.mock import patch

from nrc_grabber import dates


class DatesTests(unittest.TestCase):
    def test_is_saturday(self):
        self.assertTrue(dates.is_saturday(dt.date(2026, 9, 5)))  # Sat 5 Sep 2026
        self.assertFalse(dates.is_saturday(dt.date(2026, 9, 4)))  # Fri
        self.assertFalse(dates.is_saturday(dt.date(2026, 9, 7)))  # Mon

    def test_request_dates_no_lookback(self):
        today = dt.date(2026, 9, 5)
        self.assertEqual(dates.request_dates(today, 0), [today])

    def test_request_dates_with_lookback(self):
        today = dt.date(2026, 9, 5)
        result = dates.request_dates(today, 3)
        self.assertEqual(result, [
            dt.date(2026, 9, 5), dt.date(2026, 9, 4),
            dt.date(2026, 9, 3), dt.date(2026, 9, 2),
        ])

    def test_request_dates_negative_lookback_clamped(self):
        today = dt.date(2026, 9, 5)
        self.assertEqual(dates.request_dates(today, -1), [today])

    def test_edition_date_from_rfc(self):
        d = dates.edition_date_for_request(dt.date(2026, 9, 5), "Sat, 05 Sep 2026 00:00:00 GMT")
        self.assertEqual(d, dt.date(2026, 9, 5))

    def test_edition_date_from_iso(self):
        d = dates.edition_date_for_request(dt.date(2026, 9, 7), "2026-09-05T00:00:00")
        self.assertEqual(d, dt.date(2026, 9, 5))

    def test_edition_date_fallback_to_request(self):
        d = dates.edition_date_for_request(dt.date(2026, 9, 7), None)
        self.assertEqual(d, dt.date(2026, 9, 7))
        d = dates.edition_date_for_request(dt.date(2026, 9, 7), "garbage")
        self.assertEqual(d, dt.date(2026, 9, 7))

    def test_sunday_resolves_to_saturday(self):
        # Sunday 2026-09-06 request resolves to Saturday 2026-09-05 edition
        sunday = dt.date(2026, 9, 6)
        saturday = dt.date(2026, 9, 5)
        edition = dates.edition_date_for_request(sunday, "Sat, 05 Sep 2026 00:00:00 GMT")
        self.assertEqual(edition, saturday)
        self.assertTrue(dates.is_saturday(edition))

    def test_expected_edition_date_weekday(self):
        # Tuesday 2026-09-08 -> expects that day's edition
        self.assertEqual(dates.expected_edition_date(dt.date(2026, 9, 8)), dt.date(2026, 9, 8))

    def test_expected_edition_date_saturday(self):
        self.assertEqual(dates.expected_edition_date(dt.date(2026, 9, 5)), dt.date(2026, 9, 5))

    def test_expected_edition_date_sunday_resolves_to_saturday(self):
        self.assertEqual(dates.expected_edition_date(dt.date(2026, 9, 6)), dt.date(2026, 9, 5))

    def test_expected_edition_date_monday_is_none(self):
        self.assertIsNone(dates.expected_edition_date(dt.date(2026, 9, 7)))

    def test_effective_timezone_valid(self):
        zone, used_fallback = dates.effective_timezone("Europe/Amsterdam")
        self.assertFalse(used_fallback)
        self.assertEqual(getattr(zone, "key", None), "Europe/Amsterdam")

    def test_effective_timezone_invalid_falls_back_to_utc(self):
        zone, used_fallback = dates.effective_timezone("Not/AZone")
        self.assertTrue(used_fallback)
        self.assertEqual(zone, dt.timezone.utc)

    def test_today_local_warns_on_fallback(self):
        # The one-shot (RUN_ONCE) path calls today_local() directly, so it
        # must not silently fall back to UTC the way a bare
        # effective_timezone() call would; it needs its own warning.
        with patch("nrc_grabber.dates.sys.stderr") as mock_stderr:
            dates.today_local("Not/AZone")
        self.assertTrue(mock_stderr.write.called)

    def test_today_local_does_not_warn_for_a_valid_zone(self):
        with patch("nrc_grabber.dates.sys.stderr") as mock_stderr:
            dates.today_local("Europe/Amsterdam")
        self.assertFalse(mock_stderr.write.called)

    def test_today_local_agrees_with_effective_timezone_and_expected_edition(self):
        # The scheduler resolves "today" via effective_timezone(); the
        # one-shot/backfill path resolves it via today_local(). Both must
        # agree on the same concrete zone and date, so a scheduled run and
        # an external one-shot run never disagree about which edition is
        # expected for "today".
        zone, used_fallback = dates.effective_timezone("Europe/Amsterdam")
        self.assertFalse(used_fallback)
        today = dates.today_local("Europe/Amsterdam")
        self.assertEqual(today, dt.datetime.now(zone).date())
        expected = dates.expected_edition_date(today)
        self.assertIn(expected, (None, today, today - dt.timedelta(days=1)))

    def test_resolve_local_instant_normal_time(self):
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Amsterdam")
        result = dates.resolve_local_instant(zone, 2026, 9, 8, 6, 0)
        self.assertEqual(result, dt.datetime(2026, 9, 8, 4, 0, tzinfo=dt.timezone.utc))

    def test_resolve_local_instant_nonexistent_spring_gap_picks_first_valid_instant(self):
        # 2026-03-29 02:30 Amsterdam doesn't exist (clocks jump 02:00->03:00).
        # The first valid concrete instant after the gap is 03:00, not 03:30
        # (which would merely be the target wall-clock time shifted forward
        # by the length of the gap).
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Amsterdam")
        result = dates.resolve_local_instant(zone, 2026, 3, 29, 2, 30)
        self.assertEqual(result, dt.datetime(2026, 3, 29, 1, 0, tzinfo=dt.timezone.utc))
        local = result.astimezone(zone)
        self.assertEqual((local.hour, local.minute), (3, 0))

    def test_resolve_local_instant_nonmidpoint_gap_minutes_all_pick_exact_transition(self):
        # Every nonexistent minute inside the spring gap (not just the
        # midpoint 02:30) must resolve to exactly the transition instant
        # (2026-03-29T01:00:00Z / 03:00 local), with zero fractional-second
        # drift; a bisection that stops at ~1 second of remaining interval
        # instead of the exact boundary would fail this for non-midpoint
        # minutes even though it happens to pass for 02:30.
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Amsterdam")
        expected = dt.datetime(2026, 3, 29, 1, 0, 0, tzinfo=dt.timezone.utc)
        for minute in (1, 10, 29, 30, 59):
            with self.subTest(minute=minute):
                result = dates.resolve_local_instant(zone, 2026, 3, 29, 2, minute)
                self.assertEqual(result, expected)
                self.assertEqual(result.microsecond, 0)
                local = result.astimezone(zone)
                self.assertEqual((local.hour, local.minute), (3, 0))

    def test_resolve_local_instant_ambiguous_autumn_resolves_to_fold_zero(self):
        # 2026-10-25 02:30 Amsterdam occurs twice (fall back); resolved
        # deterministically to the first (fold=0, earlier-UTC-offset)
        # occurrence.
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Amsterdam")
        result = dates.resolve_local_instant(zone, 2026, 10, 25, 2, 30)
        self.assertEqual(result, dt.datetime(2026, 10, 25, 0, 30, tzinfo=dt.timezone.utc))
        local = result.astimezone(zone)
        self.assertEqual((local.hour, local.minute), (2, 30))


if __name__ == "__main__":
    unittest.main()
