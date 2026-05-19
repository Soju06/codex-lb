# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.10.9 AS uv-bin

FROM oven/bun:1.3.14-alpine AS frontend-build

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

COPY --from=uv-bin /uv /uvx /usr/local/bin/

RUN python -m venv --without-pip /opt/venv
ENV PATH="/opt/venv/bin:/usr/local/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra metrics --extra tracing

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --only-upgrade \
        libc-bin libc6 libcap2 libsystemd0 libudev1 sed \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip uninstall -y pip setuptools wheel || true \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
    && rm -rf /usr/local/lib/python*/site-packages/pip* \
        /usr/local/lib/python*/site-packages/setuptools* \
        /usr/local/lib/python*/site-packages/wheel*

RUN adduser --disabled-password --gecos "" app \
    && mkdir -p /var/lib/codex-lb \
    && chown -R app:app /var/lib/codex-lb

COPY --from=python-build /opt/venv /opt/venv
COPY app app
COPY config config
COPY scripts scripts
COPY --from=frontend-build /app/app/static app/static

RUN chmod +x /app/scripts/docker-entrypoint.sh

USER app
EXPOSE 2455 1455

CMD ["/app/scripts/docker-entrypoint.sh"]
