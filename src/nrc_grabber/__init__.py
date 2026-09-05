"""NRC Grabber entry point: download today's edition and prune old copies."""

from __future__ import annotations

import http.cookiejar
import sys
from pathlib import Path

from . import config as _config
from . import dates as _dates
from . import nrc as _nrc
from . import prune as _prune


def _existing_for_edition(dest_dir: Path, fmt: str, edition_date) -> Path | None:
    """Return an existing completed regular file for the edition date, or None.

    Matches any owned-format file whose parsed date equals the edition date,
    not just the canonical filename, so a noncanonical-but-safe Content-Disposition
    name from a prior run is still treated as completed.
    """
    import stat as _stat

    from .prune import parse_edition_date

    for entry in dest_dir.iterdir():
        try:
            st = entry.lstat()
        except OSError:
            continue
        if not _stat.S_ISREG(st.st_mode):
            continue
        d = parse_edition_date(fmt, entry.name)
        if d == edition_date:
            return entry
    return None


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = _config.load()
    except _config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    print(f"nrc-grabber: format={cfg.fmt} output={cfg.output_dir} "
          f"keep_saturday={cfg.keep_saturday} keep_weekday={cfg.keep_weekday} "
          f"lookback={cfg.lookback_days}")
    cookiejar = http.cookiejar.CookieJar()
    # Login
    try:
        _nrc.login(cookiejar, cfg.username, cfg.password)
        print("login: ok")
    except _nrc.AuthError as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 2
    except _nrc.NrcError as e:
        print(f"login error: {e}", file=sys.stderr)
        return 1
    # Resolve edition with lookback on genuine 404 only
    today = _dates.today_local(cfg.tz)
    candidates = _dates.request_dates(today, cfg.lookback_days)
    manifest = None
    resolved_date = None
    for d in candidates:
        try:
            m = _nrc.edition_manifest(cookiejar, d)
        except _nrc.NrcError as e:
            # auth/server error: do NOT lookback or prune; fail
            print(f"manifest error for {d}: {e}", file=sys.stderr)
            return 1
        if m is not None:
            manifest = m
            resolved_date = d
            break
    if manifest is None:
        print("no edition available for the requested date(s); nothing to do")
        return 0
    edition_date = _nrc.edition_identity(manifest, resolved_date)
    print(f"resolved edition date: {edition_date} (request: {resolved_date})")
    # Idempotency: skip if a completed file for this edition already exists
    expected = _nrc.expected_filename(cfg.fmt, edition_date)
    dest_dir = Path(cfg.output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = _existing_for_edition(dest_dir, cfg.fmt, edition_date)
    if existing is not None:
        print(f"edition already present: {existing.name}; skipping download")
    else:
        download_path = manifest.get("download", {}).get(cfg.format_key)
        if not download_path:
            print(f"no download path for format {cfg.fmt} in manifest", file=sys.stderr)
            return 1
        try:
            final_path = _nrc.download_edition(
                cookiejar, download_path, dest_dir, cfg.fmt, edition_date
            )
            print(f"downloaded: {final_path.name}")
        except _nrc.NrcError as e:
            print(f"download failed: {e}", file=sys.stderr)
            return 1
    # Prune (even on skip)
    deleted = _prune.prune(dest_dir, cfg.fmt, cfg.keep_saturday, cfg.keep_weekday)
    if deleted:
        print(f"pruned {len(deleted)} file(s):")
        for p in deleted:
            print(f"  - {p.name}")
    else:
        print("prune: nothing to remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

