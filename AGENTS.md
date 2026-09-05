# AGENTS.md

Guidance for AI agents working in this repository.

## Project

nrc-grabber downloads the daily NRC newspaper (PDF, ePub, or mobi) for a
subscriber account, stores it, and prunes old copies with separate retention
for Saturday and weekday editions. It runs once per invocation and exits;
scheduling is external (cron, k8s, systemd).

## Build and run

```bash
# install + run locally (Python 3.14, uv)
uv sync
uv run nrc-grabber                       # uses env vars below
uv run python -m unittest discover -s tests   # 47 tests, no network

# Docker
docker build -t nrc-grabber .
docker compose up

# CI: GitHub Actions builds and pushes to ghcr.io/<owner>/nrc-grabber on push to main/master.
```

## Environment variables

| Var | Default | Notes |
| --- | --- | --- |
| `NRC_USERNAME` | required | subscriber email |
| `NRC_PASSWORD` | required | subscriber password |
| `FORMAT` | `pdf` | `pdf` / `epub` / `mobi` |
| `OUTPUT_DIR` | `/downloads` | output volume |
| `KEEP_SATURDAY` | `8` | retain N Saturday editions |
| `KEEP_WEEKDAY` | `14` | retain N weekday editions |
| `LOOKBACK_DAYS` | `0` | backfill to most-recent available if today has none |
| `TZ` | `Europe/Amsterdam` | local date for "today" |

Credentials are env-only, never committed, never logged.

## Code layout

```
src/nrc_grabber/
  __init__.py   # main(): config -> login -> resolve edition -> download -> prune -> exit code
  config.py     # env parsing, defaults, validation (nonnegative ints, format whitelist)
  nrc.py        # CAS login, edition manifest, atomic validated download, filename sanitization
  prune.py      # retention pruning by Saturday/weekday, anchored format-specific patterns
  dates.py      # local date, candidate request dates, Saturday classification, edition identity
tests/
  test_prune.py     test_dates.py     test_nrc.py     test_download.py
Dockerfile          # python:3.14-slim, uv, non-root (uid 1000), /downloads volume
docker-compose.yml
.github/workflows/docker.yml   # build + push to GHCR, no secrets
```

## NRC integration facts (recon-proven)

- **Auth**: Apereo CAS at `login.nrc.nl`. GET the login page, parse the
  `execution` hidden field, POST `username`/`password`/`execution`/
  `_eventId=submit`. The `execution` token is single-use; always fetch a
  fresh login page before POSTing. Session cookie: `nrcnl_session_id`.
- **Edition code**: `NH` for all publishing days.
- **Manifest**: `GET /de/data/NH/YYYY/MM/DD/` returns JSON with a `download`
  object (proxied, auth-required paths) and `assets` (direct S3, 403 without
  auth). Use `download[format]` only.
- **Download**: `https://www.nrc.nl` + `download[format]` with the session
  cookie. Response has `Content-Disposition: attachment; filename="..."` and
  the file body.
- **Formats and magic bytes**:
  - pdf -> `pdf_full`, body starts `%PDF`
  - epub -> `epub`, body starts `PK`
  - mobi -> `mobi`, PalmDB with `BOOKMOBI` (type+creator) at offset 60
- **Schedule**: Tue-Sat have editions; Sunday serves Saturday's edition;
  Monday is 404 (no Monday paper). The container no-ops cleanly on 404.
- **Observed filenames**: PDF `NH-YYYY-MM-DD.pdf`, epub `nrc_YYYYMMDD.epub`,
  mobi `nrc_YYYYMMDD.mobi`.
- **Network**: only `www.nrc.nl`, `nrc.nl`, `login.nrc.nl` over HTTPS. All
  requests and redirects are host-validated against this allowlist.

## Safety invariants (do not regress)

- Download to an exclusive temp file (`O_CREAT|O_EXCL`), validate magic
  bytes and Content-Length, then atomically rename. On failure, delete the
  temp and do NOT prune.
- Reject non-regular destination entries (symlinks, FIFOs) before writing.
- Unsafe `Content-Disposition` filenames (path separators, absolute, dot
  components, control chars) trigger the canonical format/date fallback,
  never a sanitized basename.
- Prune only deletes files matching anchored, format-specific patterns with
  valid calendar dates. Never touch unrelated formats, unknown names,
  invalid dates, symlinks, or non-regular files.
- Idempotency keys on edition identity (manifest `publication_date`), not
  the request date. Sunday resolves to Saturday's edition.
- Config validation rejects missing credentials and non-integer/negative
  retention before any network or deletion.
- Prune runs even when the download is skipped.

## Exit codes

- `0`: success, or no edition available (clean no-op)
- `1`: download, manifest, or other operational error
- `2`: authentication failure

## Conventions

- Python 3.14, stdlib only (no runtime dependencies).
- `uv` is the build tool and package manager.
- Unit tests use `unittest` (no pytest dependency); no network in tests.
- Keep the repo secret-free so it can be made public without changes.
- `mobi` is a PalmDB, not a zip — validate `BOOKMOBI` at offset 60, not `PK`.

## CI / publishing

`.github/workflows/docker.yml` builds and pushes to
`ghcr.io/<owner>/nrc-grabber` with tags `latest` and the full commit SHA.
It uses the auto-provided `GITHUB_TOKEN` (`packages: write`); no secrets are
required. Triggers on push to `main` and `master`.
