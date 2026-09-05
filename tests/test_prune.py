"""Tests for prune logic (no network)."""

import datetime as dt
import os
import stat
import tempfile
import unittest
from pathlib import Path

from nrc_grabber import prune


def _touch(path: Path, content: bytes = b"x"):
    path.write_bytes(content)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdf_keeps_n_saturday_and_n_weekday(self):
        # 3 Saturdays, 4 weekdays, keep 2 Saturday / 2 weekday
        sats = [dt.date(2026, 8, 1), dt.date(2026, 8, 8), dt.date(2026, 8, 15)]
        wdays = [dt.date(2026, 8, 3), dt.date(2026, 8, 4), dt.date(2026, 8, 5), dt.date(2026, 8, 6)]
        for d in sats + wdays:
            _touch(self.dir / f"NH-{d.isoformat()}.pdf")
        deleted = prune.prune(self.dir, "pdf", keep_saturday=2, keep_weekday=2)
        self.assertEqual(len(deleted), 3)  # 1 sat + 2 weekday
        remaining = sorted(p.name for p in self.dir.iterdir())
        # keep 2 newest sats (Aug 8, 15) and 2 newest weekdays (Aug 5, 6)
        expected = [
            "NH-2026-08-05.pdf", "NH-2026-08-06.pdf",
            "NH-2026-08-08.pdf", "NH-2026-08-15.pdf",
        ]
        self.assertEqual(remaining, sorted(expected))

    def test_epub_anchored_pattern(self):
        _touch(self.dir / "nrc_20260905.epub")
        _touch(self.dir / "nrc_20260904.epub")
        deleted = prune.prune(self.dir, "epub", keep_saturday=0, keep_weekday=0)
        self.assertEqual(len(deleted), 2)

    def test_mobi_anchored_pattern(self):
        _touch(self.dir / "nrc_20260905.mobi")
        deleted = prune.prune(self.dir, "mobi", keep_saturday=1, keep_weekday=1)
        self.assertEqual(len(deleted), 0)

    def test_unrelated_files_not_touched(self):
        _touch(self.dir / "NH-2026-08-15.pdf")
        _touch(self.dir / "random.pdf")
        _touch(self.dir / "notes.txt")
        _touch(self.dir / "NH-bad.pdf")
        deleted = prune.prune(self.dir, "pdf", keep_saturday=0, keep_weekday=0)
        names = {p.name for p in deleted}
        self.assertEqual(names, {"NH-2026-08-15.pdf"})
        # unrelated files remain
        for f in ["random.pdf", "notes.txt", "NH-bad.pdf"]:
            self.assertTrue((self.dir / f).exists())

    def test_invalid_date_not_deleted(self):
        _touch(self.dir / "NH-2026-02-30.pdf")  # Feb 30 invalid
        deleted = prune.prune(self.dir, "pdf", keep_saturday=0, keep_weekday=0)
        self.assertEqual(deleted, [])
        self.assertTrue((self.dir / "NH-2026-02-30.pdf").exists())

    def test_zero_retention_deletes_all(self):
        for d in [dt.date(2026, 8, 1), dt.date(2026, 8, 8), dt.date(2026, 8, 3)]:
            _touch(self.dir / f"NH-{d.isoformat()}.pdf")
        deleted = prune.prune(self.dir, "pdf", keep_saturday=0, keep_weekday=0)
        self.assertEqual(len(deleted), 3)

    def test_symlink_skipped(self):
        target = self.dir / "NH-2026-08-15.pdf"
        _touch(target)
        link = self.dir / "NH-2026-08-08.pdf"
        os.symlink(target, link)
        deleted = prune.prune(self.dir, "pdf", keep_saturday=1, keep_weekday=1)
        # symlink must NOT be deleted (not a regular file)
        self.assertTrue(link.is_symlink())
        self.assertNotIn(link, deleted)

    def test_parse_edition_date(self):
        self.assertEqual(prune.parse_edition_date("pdf", "NH-2026-09-05.pdf"), dt.date(2026, 9, 5))
        self.assertEqual(prune.parse_edition_date("epub", "nrc_20260905.epub"), dt.date(2026, 9, 5))
        self.assertEqual(prune.parse_edition_date("mobi", "nrc_20260905.mobi"), dt.date(2026, 9, 5))
        self.assertIsNone(prune.parse_edition_date("pdf", "random.pdf"))
        self.assertIsNone(prune.parse_edition_date("pdf", "NH-2026-09-05.epub"))


if __name__ == "__main__":
    unittest.main()
