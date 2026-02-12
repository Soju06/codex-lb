# syntax=docker/dockerfile:1.7
FROM oven/bun:1.3.7-alpine AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/bun.lock ./
RUN --mount=type=cache,target=/root/.bun/install/cache \
    bun install --frozen-lockfile

COPY frontend ./
RUN bun run build

FROM python:3.13-slim AS python-build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN adduser --disabled-password --gecos "" app \
    && mkdir -p /var/lib/codex-lb \
    && chown -R app:app /var/lib/codex-lb

COPY --from=python-build /opt/venv /opt/venv
COPY app app
COPY --from=frontend-build /app/app/static app/static

USER app
EXPOSE 2455 1455

CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "2455"]
