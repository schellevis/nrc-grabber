"""Retention pruning by Saturday/weekday editions."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from . import dates as _dates

# Anchored, format-specific filename patterns mapping a captured date.
# PDF: NH-YYYY-MM-DD.pdf ; epub/mobi: nrc_YYYYMMDD.{epub,mobi}
_PATTERNS = {
    "pdf": re.compile(r"^NH-(\d{4})-(\d{2})-(\d{2})\.pdf$"),
    "epub": re.compile(r"^nrc_(\d{4})(\d{2})(\d{2})\.epub$"),
    "mobi": re.compile(r"^nrc_(\d{4})(\d{2})(\d{2})\.mobi$"),
}


def parse_edition_date(fmt: str, filename: str) -> _dt.date | None:
    """Return the edition date for an owned-format file, or None if not owned."""
    pat = _PATTERNS.get(fmt)
    if pat is None:
        return None
    m = pat.match(filename)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _owned_files(output_dir: Path, fmt: str) -> list[tuple[_dt.date, Path]]:
    """Return (date, path) for valid owned-format regular files; skip symlinks/non-regular."""
    import stat as _stat

    results = []
    for entry in output_dir.iterdir():
        try:
            st = entry.lstat()
        except OSError:
            continue
        if not _stat.S_ISREG(st.st_mode):
            continue
        d = parse_edition_date(fmt, entry.name)
        if d is not None:
            results.append((d, entry))
    return results


def prune(output_dir: Path | str, fmt: str, keep_saturday: int, keep_weekday: int) -> list[Path]:
    """Delete files beyond the keep counts per category. Returns deleted paths.

    Only deletes owned-format files with valid dates. Never touches
    unrelated formats, unknown names, invalid dates, symlinks, or non-regular files.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    owned = _owned_files(output_dir, fmt)
    saturdays = sorted([r for r in owned if _dates.is_saturday(r[0])], key=lambda r: r[0], reverse=True)
    weekdays = sorted([r for r in owned if not _dates.is_saturday(r[0])], key=lambda r: r[0], reverse=True)
    to_delete: list[Path] = []
    if keep_saturday >= 0:
        to_delete.extend(p for _, p in saturdays[keep_saturday:])
    if keep_weekday >= 0:
        to_delete.extend(p for _, p in weekdays[keep_weekday:])
    deleted = []
    for p in to_delete:
        try:
            p.unlink()
            deleted.append(p)
        except OSError:
            pass
    return deleted
