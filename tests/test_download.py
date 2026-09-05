"""Mocked behavioral tests for download_edition and orchestration (no network)."""

import datetime as dt
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nrc_grabber import nrc


class _FakeResp:
    def __init__(self, body: bytes, status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        if n == -1:
            b, self._body = self._body, b""
            return b
        b, self._body = self._body[:n], self._body[n:]
        return b


def _pdf_body(n=1024):
    return b"%PDF-1.4\n" + b"x" * (n - 8)


def _make_resp(body, status=200, cd=None, cl=None):
    h = {}
    if cd:
        h["Content-Disposition"] = cd
    if cl:
        h["Content-Length"] = str(cl)
    return _FakeResp(body, status=status, headers=h)


class DownloadEditionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp)
        self.cj = object()  # cookiejar not used in mocked path
        self.edition = dt.date(2026, 9, 5)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_successful_pdf_download(self, mop, vu, mopen):
        body = _pdf_body(2048)
        mopen.return_value = _make_resp(body, cd='attachment; filename="NH-2026-09-05.pdf"', cl=len(body))
        path = nrc.download_edition(self.cj, "/de/data/s3/x/edition/x.pdf/x.pdf", self.dir, "pdf", self.edition)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "NH-2026-09-05.pdf")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), body)

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_rejects_html_body(self, mop, vu, mopen):
        mopen.return_value = _make_resp(b"<html>login page</html>", cd='attachment; filename="x.pdf"')
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        # no completed file and no temp left
        self.assertEqual(list(self.dir.iterdir()), [])

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_rejects_empty_body(self, mop, vu, mopen):
        mopen.return_value = _make_resp(b"", cd='attachment; filename="x.pdf"')
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        self.assertEqual(list(self.dir.iterdir()), [])

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_truncation_detected(self, mop, vu, mopen):
        body = _pdf_body(100)
        mopen.return_value = _make_resp(body, cd='attachment; filename="x.pdf"', cl=200)
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        self.assertEqual(list(self.dir.iterdir()), [])

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_bad_magic_rejected(self, mop, vu, mopen):
        mopen.return_value = _make_resp(b"NOTPDF" + b"x" * 100, cd='attachment; filename="x.pdf"')
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        self.assertEqual(list(self.dir.iterdir()), [])

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_http_error(self, mop, vu, mopen):
        import urllib.error

        mopen.side_effect = urllib.error.HTTPError("/x", 500, "err", {}, io.BytesIO(b""))
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_unsafe_filename_uses_fallback(self, mop, vu, mopen):
        body = _pdf_body(100)
        mopen.return_value = _make_resp(body, cd='attachment; filename="../../etc/passwd"', cl=len(body))
        path = nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        self.assertEqual(path.name, "NH-2026-09-05.pdf")

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_mobi_bookmobi_offset(self, mop, vu, mopen):
        # PalmDB: 60 bytes header + BOOKMOBI at offset 60
        body = b"NRC_Handelsblad" + b"\x00" * (60 - 15) + b"BOOKMOBI" + b"\x00" * 100
        mopen.return_value = _make_resp(body, cd='attachment; filename="nrc_20260905.mobi"', cl=len(body))
        path = nrc.download_edition(self.cj, "/x", self.dir, "mobi", self.edition)
        self.assertEqual(path.name, "nrc_20260905.mobi")

    @patch("nrc_grabber.nrc._open")
    @patch("nrc_grabber.nrc._validate_url")
    @patch("nrc_grabber.nrc._new_opener")
    def test_symlink_final_rejected(self, mop, vu, mopen):
        # pre-create a symlink at the final path
        target = self.dir / "target.txt"
        target.write_bytes(b"sentinel")
        link = self.dir / "NH-2026-09-05.pdf"
        os.symlink(target, link)
        body = _pdf_body(100)
        mopen.return_value = _make_resp(body, cd='attachment; filename="NH-2026-09-05.pdf"', cl=len(body))
        with self.assertRaises(nrc.NrcError):
            nrc.download_edition(self.cj, "/x", self.dir, "pdf", self.edition)
        # sentinel untouched
        self.assertEqual(target.read_bytes(), b"sentinel")


if __name__ == "__main__":
    unittest.main()
