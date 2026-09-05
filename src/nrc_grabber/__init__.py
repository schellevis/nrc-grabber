"""NRC Grabber entry point: download available editions and prune old copies."""

from __future__ import annotations

import http.cookiejar
import sys
from pathlib import Path
from typing import NamedTuple

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


class RunOutcome(NamedTuple):
    """Result of one _execute() pass, richer than the plain exit code.

    `edition_obtained` is measured during the pass, before pruning, and only
    for the expected resolved edition for "today" (see dates.expected_edition_date);
    an older backfill edition obtained in the same pass does not satisfy it.
    """

    exit_code: int
    downloaded: int
    edition_expected: bool
    edition_obtained: bool


def _execute(cfg: _config.Config) -> RunOutcome:
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
        return RunOutcome(exit_code=2, downloaded=0, edition_expected=False, edition_obtained=False)
    except _nrc.NrcError as e:
        print(f"login error: {e}", file=sys.stderr)
        return RunOutcome(exit_code=1, downloaded=0, edition_expected=False, edition_obtained=False)

    today = _dates.today_local(cfg.tz)
    expected_edition = _dates.expected_edition_date(today)
    edition_expected = expected_edition is not None
    edition_obtained = False

    dest_dir = Path(cfg.output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    candidates = _dates.request_dates(today, cfg.lookback_days)
    resolved_editions: set = set()
    downloaded = 0

    for request_date in candidates:
        try:
            manifest = _nrc.edition_manifest(cookiejar, request_date)
        except _nrc.NrcError as e:
            # auth/server error: do NOT continue the window or prune; fail
            print(f"manifest error for {request_date}: {e}", file=sys.stderr)
            return RunOutcome(1, downloaded, edition_expected, edition_obtained)
        if manifest is None:
            # genuine 404: no edition for this request date; keep scanning the window
            continue
        edition_date = _nrc.edition_identity(manifest, request_date)
        if edition_date in resolved_editions:
            # already handled via an earlier (later) request date in this pass
            continue
        resolved_editions.add(edition_date)
        print(f"resolved edition date: {edition_date} (request: {request_date})")

        existing = _existing_for_edition(dest_dir, cfg.fmt, edition_date)
        if existing is not None:
            print(f"edition already present: {existing.name}; skipping download")
            if edition_expected and edition_date == expected_edition:
                edition_obtained = True
            continue

        download_path = manifest.get("download", {}).get(cfg.format_key)
        if not download_path:
            print(f"no download path for format {cfg.fmt} in manifest", file=sys.stderr)
            return RunOutcome(1, downloaded, edition_expected, edition_obtained)
        try:
            final_path = _nrc.download_edition(
                cookiejar, download_path, dest_dir, cfg.fmt, edition_date
            )
            print(f"downloaded: {final_path.name}")
            downloaded += 1
            if edition_expected and edition_date == expected_edition:
                edition_obtained = True
        except _nrc.NrcError as e:
            print(f"download failed: {e}", file=sys.stderr)
            return RunOutcome(1, downloaded, edition_expected, edition_obtained)

    if not resolved_editions:
        print("no edition available for the requested date(s); nothing to do")

    # Prune runs once, after the whole window is processed, on any clean pass
    # (including all-404 and nothing-new-to-download); never after an error above.
    deleted = _prune.prune(dest_dir, cfg.fmt, cfg.keep_saturday, cfg.keep_weekday)
    if deleted:
        print(f"pruned {len(deleted)} file(s):")
        for p in deleted:
            print(f"  - {p.name}")
    else:
        print("prune: nothing to remove")
    return RunOutcome(0, downloaded, edition_expected, edition_obtained)


def run_once(cfg: _config.Config) -> int:
    """Run a single pass over the LOOKBACK window and return an exit code."""
    return _execute(cfg).exit_code


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = _config.load()
    except _config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    if cfg.run_once:
        return run_once(cfg)
    from . import scheduler as _scheduler

    return _scheduler.run_scheduler(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
