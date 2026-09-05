"""Tests for date helpers (no network)."""

import datetime as dt
import unittest

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


if __name__ == "__main__":
    unittest.main()
