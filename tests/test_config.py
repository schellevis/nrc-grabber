"""Tests for config env parsing, including the scheduler settings (no network)."""

import unittest
from dataclasses import FrozenInstanceError

from nrc_grabber.config import Config, ConfigError, load


BASE_ENV = {"NRC_USERNAME": "user@example.com", "NRC_PASSWORD": "secret"}


def env(**overrides) -> dict:
    e = dict(BASE_ENV)
    e.update(overrides)
    return e


class ConfigConstructionTests(unittest.TestCase):
    def test_original_eight_args_still_construct_with_new_defaults(self):
        cfg = Config(
            username="u",
            password="p",
            fmt="pdf",
            output_dir="/downloads",
            keep_saturday=8,
            keep_weekday=14,
            lookback_days=0,
            tz="Europe/Amsterdam",
        )
        self.assertEqual(cfg.run_once, False)
        self.assertEqual(cfg.run_at, "06:00")
        self.assertEqual(cfg.retry_delay_minutes, 120)
        self.assertEqual(cfg.retry_attempts, 1)
        self.assertEqual(cfg.skip_weekdays, frozenset({6}))

    def test_frozen_and_password_repr_suppressed(self):
        cfg = load(env())
        with self.assertRaises(FrozenInstanceError):
            cfg.username = "other"
        self.assertNotIn("secret", repr(cfg))
        self.assertNotIn("password", repr(cfg))


class RunOnceParsingTests(unittest.TestCase):
    def test_default_is_falsey(self):
        self.assertEqual(load(env()).run_once, False)

    def test_truthy_tokens(self):
        for token in ("1", "true", "TRUE", "yes", "on", "On"):
            self.assertTrue(load(env(RUN_ONCE=token)).run_once, msg=token)

    def test_falsey_tokens(self):
        for token in ("0", "false", "FALSE", "no", "off", ""):
            self.assertFalse(load(env(RUN_ONCE=token)).run_once, msg=token)

    def test_invalid_token_raises(self):
        with self.assertRaises(ConfigError):
            load(env(RUN_ONCE="maybe"))


class RunAtParsingTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(load(env()).run_at, "06:00")

    def test_valid_custom(self):
        self.assertEqual(load(env(RUN_AT="23:59")).run_at, "23:59")

    def test_invalid_format_raises(self):
        for bad in ("25:00", "06:60", "notatime", "06-00", "06:0:00", "06:"):
            with self.assertRaises(ConfigError, msg=bad):
                load(env(RUN_AT=bad))


class RetrySettingsTests(unittest.TestCase):
    def test_defaults(self):
        cfg = load(env())
        self.assertEqual(cfg.retry_delay_minutes, 120)
        self.assertEqual(cfg.retry_attempts, 1)

    def test_zero_allowed(self):
        cfg = load(env(RETRY_DELAY_MINUTES="0", RETRY_ATTEMPTS="0"))
        self.assertEqual(cfg.retry_delay_minutes, 0)
        self.assertEqual(cfg.retry_attempts, 0)

    def test_negative_rejected(self):
        with self.assertRaises(ConfigError):
            load(env(RETRY_DELAY_MINUTES="-1"))
        with self.assertRaises(ConfigError):
            load(env(RETRY_ATTEMPTS="-1"))

    def test_noninteger_rejected(self):
        with self.assertRaises(ConfigError):
            load(env(RETRY_DELAY_MINUTES="soon"))

    def test_noninteger_retry_attempts_rejected(self):
        with self.assertRaises(ConfigError):
            load(env(RETRY_ATTEMPTS="soon"))


class SkipWeekdaysParsingTests(unittest.TestCase):
    def test_default_skips_sunday(self):
        self.assertEqual(load(env()).skip_weekdays, frozenset({6}))

    def test_explicit_empty_skips_nothing(self):
        self.assertEqual(load(env(SKIP_WEEKDAYS="")).skip_weekdays, frozenset())

    def test_names_and_numbers_mixed(self):
        cfg = load(env(SKIP_WEEKDAYS="mon,3,Sat"))
        self.assertEqual(cfg.skip_weekdays, frozenset({0, 3, 5}))

    def test_unknown_token_raises(self):
        with self.assertRaises(ConfigError):
            load(env(SKIP_WEEKDAYS="funday"))

    def test_all_seven_by_name_raises(self):
        with self.assertRaises(ConfigError):
            load(env(SKIP_WEEKDAYS="mon,tue,wed,thu,fri,sat,sun"))

    def test_all_seven_by_number_raises(self):
        with self.assertRaises(ConfigError):
            load(env(SKIP_WEEKDAYS="0,1,2,3,4,5,6"))

    def test_all_seven_mixed_raises(self):
        with self.assertRaises(ConfigError):
            load(env(SKIP_WEEKDAYS="mon,1,2,3,4,5,sun"))


if __name__ == "__main__":
    unittest.main()
