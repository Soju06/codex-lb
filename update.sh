#!/usr/bin/env bash
set -euo pipefail

container_name="${CODEX_LB_CONTAINER_NAME:-codex-lb-direct}"
image_name="${CODEX_LB_IMAGE_NAME:-codex-lb-server}"
volume_name="${CODEX_LB_VOLUME_NAME:-codex-lb-data}"
env_file="${CODEX_LB_ENV_FILE:-.env.local}"

read_env_value() {
  local key="$1"
  if [ ! -f "${env_file}" ]; then
    return 0
  fi
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${env_file}"
}

worker_args=()
if [ -n "${CODEX_LB_DIRECT_UVICORN_WORKERS:-}" ]; then
  worker_args=(-e CODEX_LB_UVICORN_WORKERS="${CODEX_LB_DIRECT_UVICORN_WORKERS}")
fi
network_args=()
network_name="${CODEX_LB_DOCKER_NETWORK:-$(read_env_value CODEX_LB_DOCKER_NETWORK)}"
if [ -n "${network_name}" ]; then
  docker network inspect "${network_name}" >/dev/null 2>&1 || docker network create "${network_name}" >/dev/null
  network_args=(--network "${network_name}")
fi

metrics_args=()
metrics_enabled="${CODEX_LB_METRICS_ENABLED:-$(read_env_value CODEX_LB_METRICS_ENABLED)}"
metrics_port="${CODEX_LB_METRICS_PORT:-$(read_env_value CODEX_LB_METRICS_PORT)}"
case "${metrics_enabled}" in
  true|TRUE|1|yes|YES)
    metrics_args=(-p "127.0.0.1:${metrics_port:-9090}:${metrics_port:-9090}")
    ;;
esac

docker build --pull -t "${image_name}" .
docker volume create "${volume_name}" >/dev/null
docker rm -f "${container_name}" >/dev/null 2>&1 || true
docker run -d --name "${container_name}" \
  --hostname "${container_name}" \
  --restart unless-stopped \
  "${network_args[@]}" \
  --env-file "${env_file}" \
  "${worker_args[@]}" \
  -p 127.0.0.1:2455:2455 \
  -p 127.0.0.1:1455:1455 \
  "${metrics_args[@]}" \
  -v "${volume_name}":/var/lib/codex-lb \
  "${image_name}"
