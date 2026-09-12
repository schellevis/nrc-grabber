# nrc-grabber

A small Docker container that downloads the daily NRC newspaper (PDF, ePub, or
mobi) for a subscriber account and prunes old copies with separate retention
for Saturday and weekday editions.

By default the container runs an **in-container daily scheduler** (daemon
mode): on startup it runs once immediately as a catch-up pass, then downloads
at a configurable local time each day, retries a configurable number of times
if the run failed or the expected edition wasn't obtained yet, and skips
Sundays (Monday has no edition of its own, so a Monday pass downloads nothing
unless `LOOKBACK_DAYS>0`, in which case it can still backfill other missing
editions in the window). The startup catch-up run is skipped if today is a
skipped weekday, and if today's own scheduled time hasn't happened yet, that
slot is skipped too (the catch-up already covers today) so it doesn't run
twice on day one. Set `RUN_ONCE=1` to instead run a single pass and exit, for
external schedulers (cron, Kubernetes, systemd).

`LOOKBACK_DAYS=N` makes every pass (scheduled or one-shot) a catch-up
backfiller: it downloads **every** available edition in the window of today
plus the previous `N` days, not just the most recent one. Editions are
deduplicated by edition identity, so Sunday (which serves Saturday's edition)
never produces a duplicate, and editions already on disk are skipped.

## Configuration (environment variables)

| Variable | Default | Description |
| --- | --- | --- |
| `NRC_USERNAME` | required | NRC subscriber email |
| `NRC_PASSWORD` | required | NRC subscriber password |
| `FORMAT` | `pdf` | `pdf`, `epub`, or `mobi` |
| `OUTPUT_DIR` | `/downloads` | where files are stored (volume mount) |
| `KEEP_SATURDAY` | `8` | number of Saturday editions to retain |
| `KEEP_WEEKDAY` | `14` | number of weekday editions to retain |
| `LOOKBACK_DAYS` | `0` | downloads every available edition in the window of today plus this many previous days (catch-up backfill), deduplicated by edition |
| `TZ` | `Europe/Amsterdam` | timezone for determining "today" and the scheduler's daily run time |
| `RUN_ONCE` | `0` (falsey) | truthy (`1`/`true`/`yes`/`on`) runs one pass and exits; falsey (`0`/`false`/`no`/`off`/empty) runs the daily scheduler |
| `RUN_AT` | `06:00` | daily run time `HH:MM` (24h), in `TZ`; ignored when `RUN_ONCE` is truthy (the scheduler also runs once immediately on startup, see above) |
| `RETRY_DELAY_MINUTES` | `120` | minutes after the scheduled run to retry, if needed |
| `RETRY_ATTEMPTS` | `1` | number of retries after the initial daily run (`0` disables retries) |
| `SKIP_WEEKDAYS` | `sun` | comma-separated weekdays to skip entirely (`mon,tue,wed,thu,fri,sat,sun` and/or `0`-`6`); empty = skip nothing; all seven is rejected |

A retry is attempted only if the run failed, or the edition expected for that
day (Tue-Sat: that day; Sunday: the preceding Saturday; Monday: none) was not
obtained.

## Build

```bash
docker build -t nrc-grabber .
```

## Run

Daemon mode (default): stays up, downloads daily at `RUN_AT`:

```bash
docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e FORMAT=pdf \
  -e KEEP_SATURDAY=8 \
  -e KEEP_WEEKDAY=14 \
  -e RUN_AT=06:00 \
  -v "$PWD/downloads:/downloads" \
  nrc-grabber
```

One-shot mode, for an external scheduler (cron/k8s/systemd):

```bash
docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e RUN_ONCE=1 \
  -v "$PWD/downloads:/downloads" \
  nrc-grabber
```

## Schedule (cron example, one-shot mode)

```cron
30 5 * * * TZ=Europe/Amsterdam docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e RUN_ONCE=1 \
  -v /path/to/downloads:/downloads \
  ghcr.io/<owner>/nrc-grabber:latest
```

## CI

Pushing to `main` builds and publishes the image to GHCR as
`ghcr.io/<owner>/nrc-grabber:latest` and `ghcr.io/<owner>/nrc-grabber:<sha>`.
No secrets are required; the workflow uses the auto-provided `GITHUB_TOKEN`.

## Notes

- The tool downloads only what a subscriber is entitled to. It is for personal
  archival use; do not redistribute the downloaded content.
- Saturday and Sunday both resolve to the Saturday edition. Monday has no
  edition of its own; in one-shot mode (`RUN_ONCE=1`, `LOOKBACK_DAYS=0`) a
  Monday run exits cleanly with no file. With `LOOKBACK_DAYS>0` (or in the
  default daily scheduler, which keeps running regardless), a Monday pass
  still backfills any other missing edition in the lookback window.
- Credentials are read from environment variables only and are never logged.
