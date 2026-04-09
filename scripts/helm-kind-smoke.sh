#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?usage: scripts/helm-kind-smoke.sh <bundled|external-db>}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/deploy/helm/codex-lb"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-codex-lb-smoke}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-soju06/codex-lb}"
IMAGE_TAG="${IMAGE_TAG:-ci}"
DB_PASSWORD="${DB_PASSWORD:-smoke-password}"

helm dependency build "${CHART_DIR}" >/dev/null

wait_for_release() {
  local release="$1"
  local namespace="$2"
  kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" get pods
  helm test "${release}" --namespace "${namespace}" --kube-context "${KUBE_CONTEXT}"
}

install_bundled() {
  local namespace="codex-lb-smoke-bundled"
  local release="codex-lb-bundled"

  helm upgrade --install "${release}" "${CHART_DIR}" \
    --kube-context "${KUBE_CONTEXT}" \
    --namespace "${namespace}" \
    --create-namespace \
    -f "${CHART_DIR}/values-bundled.yaml" \
    --set image.registry="${IMAGE_REGISTRY}" \
    --set image.repository="${IMAGE_REPOSITORY}" \
    --set image.tag="${IMAGE_TAG}" \
    --set image.pullPolicy=IfNotPresent \
    --set postgresql.auth.password="${DB_PASSWORD}" \
    --set config.databaseMigrateOnStartup=false \
    --set config.sessionBridgeCodexPrewarmEnabled=false \
    --set ingress.enabled=true \
    --set ingress.ingressClassName=nginx \
    --set ingress.nginx.enabled=true \
    --set-string 'ingress.hosts[0].host=codex-lb.localtest.me' \
    --wait \
    --timeout 10m

  wait_for_release "${release}" "${namespace}"
}

install_external_db() {
  local namespace="codex-lb-smoke-external"
  local release="codex-lb-external"
  local db_release="codex-lb-smoke-db"

  helm upgrade --install "${db_release}" oci://registry-1.docker.io/bitnamicharts/postgresql \
    --kube-context "${KUBE_CONTEXT}" \
    --namespace "${namespace}" \
    --create-namespace \
    --set auth.username=codexlb \
    --set auth.password="${DB_PASSWORD}" \
    --set auth.database=codexlb \
    --set primary.persistence.enabled=false \
    --wait \
    --timeout 10m

  helm upgrade --install "${release}" "${CHART_DIR}" \
    --kube-context "${KUBE_CONTEXT}" \
    --namespace "${namespace}" \
    --create-namespace \
    -f "${CHART_DIR}/values-external-db.yaml" \
    --set image.registry="${IMAGE_REGISTRY}" \
    --set image.repository="${IMAGE_REPOSITORY}" \
    --set image.tag="${IMAGE_TAG}" \
    --set image.pullPolicy=IfNotPresent \
    --set externalDatabase.url="postgresql+asyncpg://codexlb:${DB_PASSWORD}@${db_release}-postgresql:5432/codexlb" \
    --wait \
    --timeout 10m

  wait_for_release "${release}" "${namespace}"
}

case "${MODE}" in
  bundled)
    install_bundled
    ;;
  external-db)
    install_external_db
    ;;
  *)
    echo "unsupported mode: ${MODE}" >&2
    exit 1
    ;;
esac
