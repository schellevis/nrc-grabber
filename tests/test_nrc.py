"""Tests for NRC parsing/validation functions and config (no network)."""

import datetime as dt
import unittest

from nrc_grabber import config, nrc


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = config.load({
            "NRC_USERNAME": "u@e.nl", "NRC_PASSWORD": "pw",
        })
        self.assertEqual(cfg.fmt, "pdf")
        self.assertEqual(cfg.output_dir, "/downloads")
        self.assertEqual(cfg.keep_saturday, 8)
        self.assertEqual(cfg.keep_weekday, 14)
        self.assertEqual(cfg.lookback_days, 0)
        self.assertEqual(cfg.tz, "Europe/Amsterdam")
        self.assertEqual(cfg.format_key, "pdf_full")

    def test_missing_creds_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load({})
        with self.assertRaises(config.ConfigError):
            config.load({"NRC_USERNAME": "u@e.nl"})

    def test_invalid_format(self):
        with self.assertRaises(config.ConfigError):
            config.load({"NRC_USERNAME": "u", "NRC_PASSWORD": "p", "FORMAT": "docx"})

    def test_negative_retention_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load({"NRC_USERNAME": "u", "NRC_PASSWORD": "p", "KEEP_SATURDAY": "-1"})

    def test_noninteger_retention_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load({"NRC_USERNAME": "u", "NRC_PASSWORD": "p", "KEEP_WEEKDAY": "abc"})

    def test_zero_retention_allowed(self):
        cfg = config.load({"NRC_USERNAME": "u", "NRC_PASSWORD": "p", "KEEP_SATURDAY": "0"})
        self.assertEqual(cfg.keep_saturday, 0)

    def test_format_key_mapping(self):
        for f, k in [("pdf", "pdf_full"), ("epub", "epub"), ("mobi", "mobi")]:
            cfg = config.load({"NRC_USERNAME": "u", "NRC_PASSWORD": "p", "FORMAT": f})
            self.assertEqual(cfg.format_key, k)


class NrcParsingTests(unittest.TestCase):
    def test_parse_execution_token(self):
        html = '<input name="execution" value="abc123def" type="hidden">'
        self.assertEqual(nrc.parse_execution_token(html), "abc123def")

    def test_parse_execution_token_missing(self):
        self.assertEqual(nrc.parse_execution_token("<form>no token</form>"), "")

    def test_parse_content_disposition(self):
        self.assertEqual(
            nrc.parse_content_disposition('attachment; filename="NH-2026-09-05.pdf"'),
            "NH-2026-09-05.pdf",
        )
        self.assertIsNone(nrc.parse_content_disposition(None))
        self.assertIsNone(nrc.parse_content_disposition(""))

    def test_sanitize_filename_normal(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.sanitize_filename("NH-2026-09-05.pdf", "pdf", d), "NH-2026-09-05.pdf")

    def test_sanitize_filename_path_separator(self):
        d = dt.date(2026, 9, 5)
        # unsafe names trigger fallback, not basename
        self.assertEqual(nrc.sanitize_filename("../../etc/passwd", "pdf", d), "NH-2026-09-05.pdf")

    def test_sanitize_filename_dotdot(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.sanitize_filename("..", "pdf", d), "NH-2026-09-05.pdf")

    def test_sanitize_filename_backslash(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.sanitize_filename("folder\\file.pdf", "pdf", d), "NH-2026-09-05.pdf")

    def test_sanitize_filename_missing_fallback(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.sanitize_filename(None, "pdf", d), "NH-2026-09-05.pdf")
        self.assertEqual(nrc.sanitize_filename(None, "epub", d), "nrc_20260905.epub")
        self.assertEqual(nrc.sanitize_filename(None, "mobi", d), "nrc_20260905.mobi")

    def test_sanitize_filename_absolute(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.sanitize_filename("/etc/shadow", "pdf", d), "NH-2026-09-05.pdf")

    def test_expected_filename(self):
        d = dt.date(2026, 9, 5)
        self.assertEqual(nrc.expected_filename("pdf", d), "NH-2026-09-05.pdf")
        self.assertEqual(nrc.expected_filename("epub", d), "nrc_20260905.epub")

    def test_magic_bytes_validation_pdf(self):
        self.assertEqual(config.MAGIC_PREFIX["pdf"], b"%PDF")
        self.assertEqual(config.MAGIC_PREFIX["epub"], b"PK")
        self.assertEqual(config.MAGIC_OFFSET["mobi"], (60, b"BOOKMOBI"))

    def test_allowed_hosts(self):
        self.assertIn("www.nrc.nl", nrc.ALLOWED_HOSTS)
        self.assertIn("login.nrc.nl", nrc.ALLOWED_HOSTS)


class HostValidationTests(unittest.TestCase):
    def test_validate_url_rejects_http(self):
        with self.assertRaises(nrc.NrcError):
            nrc._validate_url("http://www.nrc.nl/test")

    def test_validate_url_rejects_unapproved_host(self):
        with self.assertRaises(nrc.NrcError):
            nrc._validate_url("https://evil.example.com/x")

    def test_validate_url_accepts_allowed(self):
        nrc._validate_url("https://www.nrc.nl/de/data/NH/2026/09/05/")
        nrc._validate_url("https://login.nrc.nl/login")


if __name__ == "__main__":
    unittest.main()
