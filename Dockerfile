FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home --home-dir /home/app app

# tzdata is required so Python's zoneinfo can resolve TZ (e.g. Europe/Amsterdam)
# for the scheduler's daily run time and DST handling; the slim base image
# does not ship it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --no-dev --frozen && \
    mkdir -p /downloads && chown -R 1000:1000 /downloads /app

USER app

VOLUME ["/downloads"]

# By default the container runs the in-container daily scheduler and stays up
# (see RUN_AT/RETRY_*/SKIP_WEEKDAYS below). Set RUN_ONCE=1 to run a single
# pass and exit instead, for external schedulers (cron/k8s/systemd).
ENTRYPOINT ["nrc-grabber"]
