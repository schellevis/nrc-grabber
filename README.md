# nrc-grabber

A small Docker container that downloads the daily NRC newspaper (PDF, ePub, or
mobi) for a subscriber account and prunes old copies with separate retention
for Saturday and weekday editions. It runs **once per invocation** and exits;
schedule it with cron, Kubernetes, or systemd.

## Configuration (environment variables)

| Variable | Default | Description |
| --- | --- | --- |
| `NRC_USERNAME` | required | NRC subscriber email |
| `NRC_PASSWORD` | required | NRC subscriber password |
| `FORMAT` | `pdf` | `pdf`, `epub`, or `mobi` |
| `OUTPUT_DIR` | `/downloads` | where files are stored (volume mount) |
| `KEEP_SATURDAY` | `8` | number of Saturday editions to retain |
| `KEEP_WEEKDAY` | `14` | number of weekday editions to retain |
| `LOOKBACK_DAYS` | `0` | if today has no edition, look back this many days |
| `TZ` | `Europe/Amsterdam` | timezone for determining "today" |

## Build

```bash
docker build -t nrc-grabber .
```

## Run

```bash
docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e FORMAT=pdf \
  -e KEEP_SATURDAY=8 \
  -e KEEP_WEEKDAY=14 \
  -v "$PWD/downloads:/downloads" \
  nrc-grabber
```

## Schedule (cron example)

Run daily at 07:30 Amsterdam time (handle DST yourself or use a TZ-aware
scheduler):

```cron
30 5 * * * TZ=Europe/Amsterdam docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
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
  edition (the container exits cleanly with no file).
- Credentials are read from environment variables only and are never logged.
