FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev && \
    mkdir -p /downloads && chown -R 1000:1000 /downloads /app

USER app

VOLUME ["/downloads"]

ENTRYPOINT ["nrc-grabber"]
