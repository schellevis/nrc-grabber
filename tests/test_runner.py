"""Backfill regression tests; network replaced, files and pruning are real."""
import datetime as dt
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import nrc_grabber as app
from nrc_grabber.config import load
from nrc_grabber.nrc import AuthError, NrcError


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                         'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '6'})
        self.downloads = []

    def manifest(self, cj, day):
        if day.weekday() == 0:
            return None
        edition = day - dt.timedelta(days=1) if day.weekday() == 6 else day
        return {'publication_date': edition.isoformat(), 'download': {'pdf_full': '/paper'}}

    def download(self, cj, url, folder, fmt, day):
        self.downloads.append(day)
        path = folder / f'NH-{day}.pdf'
        path.write_bytes(b'%PDF-test')
        return path

    def run_round(self, manifest=None, download=None, login=None, today=dt.date(2026, 9, 8)):
        return self.execute_round(manifest=manifest, download=download, login=login, today=today).exit_code

    def execute_round(self, manifest=None, download=None, login=None, today=dt.date(2026, 9, 8)):
        with patch.object(app._nrc, 'login', side_effect=login) as mock_login, \
             patch.object(app._dates, 'today_local', return_value=today), \
             patch.object(app._nrc, 'edition_manifest', side_effect=manifest or self.manifest), \
             patch.object(app._nrc, 'download_edition', side_effect=download or self.download):
            outcome = app._execute(self.cfg)
            self.last_login_calls = mock_login.call_count
            return outcome

    def test_downloads_entire_window_and_deduplicates_weekend(self):
        self.assertEqual(self.run_round(), 0)
        self.assertEqual(self.downloads, [dt.date(2026, 9, d) for d in (8, 5, 4, 3, 2)])
        self.assertEqual(len(list(self.folder.iterdir())), 5)
        self.assertEqual(self.run_round(), 0)
        self.assertEqual(len(self.downloads), 5)

    def test_failure_after_success_does_not_prune(self):
        old = self.folder / 'NH-2020-01-01.pdf'
        old.write_bytes(b'%PDF-old')
        def download(*args):
            if self.downloads:
                raise NrcError('broken download')
            return self.download(*args)
        self.assertEqual(self.run_round(download=download), 1)
        self.assertTrue(old.exists())

    def test_existing_editions_still_prune(self):
        self.run_round()
        old = self.folder / 'NH-2020-01-01.pdf'
        old.write_bytes(b'%PDF-old')
        from dataclasses import replace
        self.cfg = replace(self.cfg, keep_weekday=4)
        self.assertEqual(self.run_round(), 0)
        self.assertFalse(old.exists())

    def test_manifest_failure_does_not_prune(self):
        old = self.folder / 'NH-2020-01-01.pdf'
        old.write_bytes(b'%PDF-old')
        def fail(*args):
            raise NrcError('server error')
        self.assertEqual(self.run_round(manifest=fail), 1)
        self.assertTrue(old.exists())

    def test_auth_failure_returns_exit_2_and_does_not_prune(self):
        old = self.folder / 'NH-2020-01-01.pdf'
        old.write_bytes(b'%PDF-old')
        outcome = self.execute_round(login=AuthError('bad credentials'))
        self.assertEqual(outcome.exit_code, 2)
        self.assertTrue(old.exists())

    def test_failure_after_success_calls_prune_zero_times(self):
        def download(*args):
            if self.downloads:
                raise NrcError('broken download')
            return self.download(*args)
        with patch.object(app._prune, 'prune', wraps=app._prune.prune) as mock_prune:
            outcome = self.execute_round(download=download)
        self.assertEqual(outcome.exit_code, 1)
        mock_prune.assert_not_called()

    def test_manifest_failure_calls_prune_zero_times(self):
        def fail(*args):
            raise NrcError('server error')
        with patch.object(app._prune, 'prune', wraps=app._prune.prune) as mock_prune:
            outcome = self.execute_round(manifest=fail)
        self.assertEqual(outcome.exit_code, 1)
        mock_prune.assert_not_called()

    def test_auth_failure_calls_prune_zero_times(self):
        with patch.object(app._prune, 'prune', wraps=app._prune.prune) as mock_prune:
            outcome = self.execute_round(login=AuthError('bad credentials'))
        self.assertEqual(outcome.exit_code, 2)
        mock_prune.assert_not_called()

    def test_all_404_clean_pass_still_prunes(self):
        # Nothing available anywhere in the window (e.g. a full outage window);
        # no error occurred, so the (empty) pass must still prune (KEEP_WEEKDAY=0
        # makes the effect observable).
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '6',
                          'KEEP_WEEKDAY': '0'})
        old = self.folder / 'NH-2020-01-01.pdf'
        old.write_bytes(b'%PDF-old')
        outcome = self.execute_round(manifest=lambda cj, day: None)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.downloaded, 0)
        self.assertFalse(old.exists())

    def test_all_404_clean_pass_calls_prune_exactly_once(self):
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '6'})
        with patch.object(app._prune, 'prune', wraps=app._prune.prune) as mock_prune:
            outcome = self.execute_round(manifest=lambda cj, day: None)
        self.assertEqual(outcome.exit_code, 0)
        mock_prune.assert_called_once()

    def test_duplicate_manifest_dedup_uses_in_memory_set_not_disk(self):
        # The mocked downloader deliberately writes a filename that does NOT
        # match the anchored owned-format pattern, so disk-based dedup alone
        # cannot prevent a second download of the same resolved edition; only
        # an in-memory resolved-edition set can.
        def manifest(cj, day):
            # Every request date in the window resolves to the same edition.
            return {'publication_date': dt.date(2026, 9, 8).isoformat(), 'download': {'pdf_full': '/paper'}}

        def download(cj, url, folder, fmt, day):
            self.downloads.append(day)
            path = folder / f'unrecognized-{len(self.downloads)}.pdf'
            path.write_bytes(b'%PDF-test')
            return path

        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '3'})
        outcome = self.execute_round(manifest=manifest, download=download)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.downloaded, 1)
        self.assertEqual(self.downloads, [dt.date(2026, 9, 8)])

    def test_login_happens_once_per_pass(self):
        self.run_round()
        self.assertEqual(self.last_login_calls, 1)

    def test_expected_edition_obtained_survives_zero_retention_prune(self):
        # KEEP_WEEKDAY=0 deletes the just-downloaded weekday edition; edition_obtained
        # must have been measured before pruning, so it stays True (no false retry).
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '0',
                          'KEEP_WEEKDAY': '0'})
        outcome = self.execute_round()
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.edition_expected)
        self.assertTrue(outcome.edition_obtained)
        # the edition really was pruned away
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_expected_edition_already_present_counts_as_obtained(self):
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '0'})
        self.run_round()
        outcome = self.execute_round()
        self.assertEqual(outcome.downloaded, 0)
        self.assertTrue(outcome.edition_obtained)

    def test_only_older_backfill_edition_obtained_still_needs_retry(self):
        # Today's edition (Tue 09-08) is unavailable (404); only an older
        # backfill edition downloads. edition_obtained must stay False.
        def manifest(cj, day):
            if day == dt.date(2026, 9, 8):
                return None
            return self.manifest(cj, day)

        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '3'})
        outcome = self.execute_round(manifest=manifest)
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.edition_expected)
        self.assertFalse(outcome.edition_obtained)
        self.assertGreater(outcome.downloaded, 0)

    def test_main_dispatches_run_once_when_truthy(self):
        with patch.object(app._config, 'load', return_value=replace(self.cfg, run_once=True)), \
             patch.object(app, 'run_once', return_value=0) as mock_run_once, \
             patch('nrc_grabber.scheduler.run_scheduler') as mock_scheduler:
            self.assertEqual(app.main(), 0)
            mock_run_once.assert_called_once()
            mock_scheduler.assert_not_called()

    def test_main_dispatches_scheduler_when_falsey(self):
        with patch.object(app._config, 'load', return_value=replace(self.cfg, run_once=False)), \
             patch.object(app, 'run_once') as mock_run_once, \
             patch('nrc_grabber.scheduler.run_scheduler', return_value=0) as mock_scheduler:
            self.assertEqual(app.main(), 0)
            mock_run_once.assert_not_called()
            mock_scheduler.assert_called_once()

    def test_main_config_error_returns_1(self):
        with patch.object(app._config, 'load', side_effect=app._config.ConfigError('boom')):
            self.assertEqual(app.main(), 1)

    def test_invalid_config_starts_neither_scheduling_nor_network(self):
        with patch.object(app._config, 'load', side_effect=app._config.ConfigError('boom')), \
             patch.object(app, 'run_once') as mock_run_once, \
             patch('nrc_grabber.scheduler.run_scheduler') as mock_scheduler, \
             patch.object(app._nrc, 'login') as mock_login:
            self.assertEqual(app.main(), 1)
            mock_run_once.assert_not_called()
            mock_scheduler.assert_not_called()
            mock_login.assert_not_called()

    def test_main_propagates_nonzero_one_shot_exit_code(self):
        with patch.object(app._config, 'load', return_value=replace(self.cfg, run_once=True)), \
             patch.object(app, 'run_once', return_value=7) as mock_run_once, \
             patch('nrc_grabber.scheduler.run_scheduler') as mock_scheduler:
            self.assertEqual(app.main(), 7)
            mock_run_once.assert_called_once()
            mock_scheduler.assert_not_called()

    def test_run_once_returns_int_exit_code_and_ignores_scheduler_settings(self):
        # run_once() is the real public one-shot entry point (not _execute
        # directly): it must return a plain int exit code, and scheduler-only
        # settings (run_at/retry_*) must have no effect on its behavior.
        cfg = replace(self.cfg, run_once=True, run_at='23:59',
                      retry_attempts=99, retry_delay_minutes=99)
        with patch.object(app._nrc, 'login'), \
             patch.object(app._dates, 'today_local', return_value=dt.date(2026, 9, 8)), \
             patch.object(app._nrc, 'edition_manifest', side_effect=self.manifest), \
             patch.object(app._nrc, 'download_edition', side_effect=self.download):
            result = app.run_once(cfg)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 0)

    def test_execute_agrees_on_utc_fallback_date_and_expected_edition_with_fixed_clock(self):
        # A missing/invalid TZ must not silently produce a mismatched "today"
        # between the timezone fallback and the edition it expects: fix the
        # clock (so this doesn't depend on when the suite happens to run)
        # to a known instant where the real Amsterdam date and the UTC
        # fallback date differ (Sunday 23:30 UTC is already Monday in
        # Amsterdam), and assert the requested date, expected edition, and
        # fallback warning all agree on the UTC-fallback reading.
        fixed = dt.datetime(2026, 9, 6, 23, 30, tzinfo=dt.timezone.utc)  # Sunday, UTC

        class FixedDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '0',
                          'TZ': 'Not/AZone'})
        seen_requests = []

        def manifest(cj, day):
            seen_requests.append(day)
            return {'publication_date': dt.date(2026, 9, 5).isoformat(), 'download': {'pdf_full': '/paper'}}

        with patch('datetime.datetime', FixedDatetime), \
             patch.object(app._nrc, 'login'), \
             patch.object(app._nrc, 'edition_manifest', side_effect=manifest), \
             patch.object(app._nrc, 'download_edition', side_effect=self.download), \
             patch('nrc_grabber.dates.sys.stderr') as mock_stderr:
            outcome = app._execute(self.cfg)

        self.assertEqual(outcome.exit_code, 0)
        # Requested date is the UTC-fallback "today" (2026-09-06, Sunday),
        # not the real clock's date and not a naive UTC-offset guess.
        self.assertEqual(seen_requests, [dt.date(2026, 9, 6)])
        # Expected edition (Sunday -> preceding Saturday) agrees with the
        # manifest's resolved edition date, so it counts as obtained.
        self.assertTrue(outcome.edition_expected)
        self.assertTrue(outcome.edition_obtained)
        self.assertTrue(mock_stderr.write.called)

    def test_monday_has_no_expected_edition(self):
        # Monday: dates.expected_edition_date returns None; a clean no-op must
        # not be treated as a missed expected edition.
        self.cfg = load({'NRC_USERNAME': 'test', 'NRC_PASSWORD': 'test',
                          'OUTPUT_DIR': self.tmp.name, 'LOOKBACK_DAYS': '0'})
        outcome = self.execute_round(manifest=lambda cj, day: None, today=dt.date(2026, 9, 7))
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(outcome.edition_expected)
        self.assertFalse(outcome.edition_obtained)
