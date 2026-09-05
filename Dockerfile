FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --no-dev --frozen && \
    mkdir -p /downloads && chown -R 1000:1000 /downloads /app

USER app

VOLUME ["/downloads"]

ENTRYPOINT ["nrc-grabber"]
