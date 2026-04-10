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

dump_namespace_debug() {
  local namespace="$1"
  echo "[helm-kind-smoke] dumping namespace state for ${namespace}" >&2
  kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" get all || true
  kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" get events --sort-by=.lastTimestamp || true

  local pod_names
  pod_names=$(kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" get pods -o name 2>/dev/null || true)
  if [[ -n "${pod_names}" ]]; then
    while IFS= read -r pod; do
      [[ -z "${pod}" ]] && continue
      kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" describe "${pod}" || true
      kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" logs "${pod}" --all-containers=true --tail=200 || true
      kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" logs "${pod}" --all-containers=true --previous --tail=200 || true
    done <<< "${pod_names}"
  fi
}

install_bundled() {
  local namespace="codex-lb-smoke-bundled"
  local release="codex-lb-bundled"

  trap 'dump_namespace_debug "${namespace}"' ERR

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
    --set config.sessionBridgeCodexPrewarmEnabled=false \
    --set ingress.enabled=true \
    --set ingress.ingressClassName=nginx \
    --set ingress.nginx.enabled=true \
    --set-string 'ingress.hosts[0].host=codex-lb.localtest.me' \
    --set-string 'ingress.hosts[0].paths[0].path=/' \
    --set-string 'ingress.hosts[0].paths[0].pathType=Prefix' \
    --wait \
    --timeout 10m

  wait_for_release "${release}" "${namespace}"
  trap - ERR
}

install_external_db() {
  local namespace="codex-lb-smoke-external"
  local release="codex-lb-external"
  local db_release="codex-lb-smoke-db"
  local app_secret="codex-lb-external-secrets"
  local encryption_key

  trap 'dump_namespace_debug "${namespace}"' ERR

  encryption_key=$(python - <<'PY'
import base64
import os

print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)

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

  kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" delete secret "${app_secret}" --ignore-not-found
  kubectl --context "${KUBE_CONTEXT}" -n "${namespace}" create secret generic "${app_secret}" \
    --from-literal=database-url="postgresql+asyncpg://codexlb:${DB_PASSWORD}@${db_release}-postgresql:5432/codexlb" \
    --from-literal=encryption-key="${encryption_key}"

  helm upgrade --install "${release}" "${CHART_DIR}" \
    --kube-context "${KUBE_CONTEXT}" \
    --namespace "${namespace}" \
    --create-namespace \
    -f "${CHART_DIR}/values-external-db.yaml" \
    --set image.registry="${IMAGE_REGISTRY}" \
    --set image.repository="${IMAGE_REPOSITORY}" \
    --set image.tag="${IMAGE_TAG}" \
    --set image.pullPolicy=IfNotPresent \
    --set auth.existingSecret="${app_secret}" \
    --wait \
    --timeout 10m

  wait_for_release "${release}" "${namespace}"
  trap - ERR
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
