# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.15.1-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.15.1-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HAVERBOT_DATABASE=/data/haverbot.sqlite3 PATH=/app/.venv/bin:$PATH
RUN groupadd --gid 10001 haverbot \
    && useradd --uid 10001 --gid haverbot --no-create-home haverbot \
    && mkdir /data && chown haverbot:haverbot /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
VOLUME ["/data"]
CMD ["haverbot"]
