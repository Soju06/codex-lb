# Kubernetes Deployment Guide

Production deployment manifests for codex-lb on Kubernetes.

---

## Prerequisites

- Kubernetes 1.25+
- `kubectl` configured for your cluster
- **PostgreSQL** instance accessible from the cluster (required for multi-replica; SQLite is single-pod only)
- `python` with `cryptography` installed locally (for Fernet key generation)
- (Optional) Prometheus Operator for metrics via ServiceMonitor

---

## Manifests

| File | Kind | Purpose |
|------|------|---------|
| `secret.example.yaml` | Secret (template) | Shared encryption key + DB URL — copy and fill before deploying |
| `configmap.yaml` | ConfigMap | All `CODEX_LB_` environment variables (non-sensitive) |
| `job-migrate.yaml` | Job | One-shot database migration — run before Deployment rollout |
| `deployment.yaml` | Deployment | 3 replicas, Secret volume, rolling update (maxUnavailable: 0) |
| `service.yaml` | Service (ClusterIP) | Exposes port 2455 for HTTP traffic |
| `hpa.yaml` | HorizontalPodAutoscaler | Scales 2–10 replicas at 70% CPU |
| `pdb.yaml` | PodDisruptionBudget | Prevents total pod eviction during maintenance |
| `service-metrics.yaml` | Service (ClusterIP) | Exposes port 9090 for Prometheus scraping |
| `servicemonitor.yaml` | ServiceMonitor | Prometheus Operator CRD for auto-discovery |

---

## Production Rollout Sequence

> Follow this order exactly. Skipping steps — especially migration before Deployment — will cause downtime or data corruption.

### Step 1 — Create the Shared Secret

All replicas must share one Fernet encryption key. If pods get different keys, decryption of stored tokens will fail.

```bash
# Generate a new Fernet key
FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
echo "Key: $FERNET_KEY"   # save this somewhere safe — it cannot be recovered

# Copy the example and fill in values
cp deploy/k8s/secret.example.yaml my-secret.yaml
# Edit my-secret.yaml:
#   CODEX_LB_DATABASE_URL: "postgresql+asyncpg://user:password@host:5432/codexlb"
#   encryption.key: "<paste FERNET_KEY here>"

kubectl apply -f my-secret.yaml
```

> **Security**: `my-secret.yaml` contains plaintext credentials. Do NOT commit it to source control. Store the key in a password manager or secrets vault.

### Step 2 — Apply ConfigMap

```bash
kubectl apply -f deploy/k8s/configmap.yaml
```

### Step 3 — Run the Migration Job

Run migrations **before** deploying the new application version:

```bash
# Substitute the actual image tag
IMAGE_TAG="v1.2.3"   # the tag you are deploying

sed "s/__IMAGE_TAG__/${IMAGE_TAG}/g" deploy/k8s/job-migrate.yaml | kubectl apply -f -

# Wait for completion (up to 5 minutes)
kubectl wait --for=condition=complete job/codex-lb-migrate --timeout=300s

# Check logs on failure
kubectl logs job/codex-lb-migrate
```

If the job fails (exit non-zero), fix the root cause (DB unreachable, bad credentials) and re-run. Do NOT proceed to Step 4 until migration succeeds.

### Step 4 — Deploy the Application

```bash
IMAGE_TAG="v1.2.3"

sed "s/__IMAGE_TAG__/${IMAGE_TAG}/g" deploy/k8s/deployment.yaml | kubectl apply -f -
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/pdb.yaml

# Watch rollout
kubectl rollout status deployment/codex-lb --timeout=300s
```

### Step 5 — Apply HPA and Observability (Optional)

```bash
kubectl apply -f deploy/k8s/hpa.yaml

# Only if Prometheus Operator is installed:
kubectl apply -f deploy/k8s/service-metrics.yaml
kubectl apply -f deploy/k8s/servicemonitor.yaml
```

---

## Rollback Procedure

### Rolling back the application

```bash
# Revert to the previous ReplicaSet
kubectl rollout undo deployment/codex-lb

# Confirm rollback
kubectl rollout status deployment/codex-lb
kubectl get pods -l app=codex-lb
```

### When to downgrade the schema

Rolling back the **application** is safe if the migration was additive (new columns, new tables with defaults). If the migration was destructive (dropped columns, renamed tables), you must also restore the database from a backup taken before Step 3.

```bash
# Always take a snapshot before running migrations in production
pg_dump -Fc codexlb > codexlb_before_$(date +%Y%m%d_%H%M%S).dump

# Restore if needed
pg_restore -d codexlb codexlb_before_<timestamp>.dump
```

### Rolling back the Secret (key rotation)

If the encryption key must be changed:

1. Take a backup of the database.
2. Generate a new Fernet key and update the Secret.
3. All stored tokens will become unreadable until re-encrypted. Accounts must be re-authenticated.

> Key rotation is a manual process. There is no automated re-encryption path today.

---

## Configuration Reference

Edit `configmap.yaml` before deploying. All values must be strings (quoted). Sensitive values (DB URL, encryption key) live in the Secret.

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| *(in Secret)* | — | `CODEX_LB_DATABASE_URL` — PostgreSQL connection string |

### Networking

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_HTTP_CONNECTOR_LIMIT` | `100` | Max total outbound HTTP connections |
| `CODEX_LB_HTTP_CONNECTOR_LIMIT_PER_HOST` | `50` | Max outbound connections per upstream host |
| `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` | `0` (unlimited) | Max concurrent inbound requests before 503 |
| `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `30` | Seconds to drain in-flight requests during shutdown |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_METRICS_ENABLED` | `false` | Enable Prometheus `/metrics` endpoint |
| `CODEX_LB_METRICS_PORT` | `9090` | Port for `/metrics` |
| `CODEX_LB_LOG_FORMAT` | `text` | Log format: `text` or `json` |
| `CODEX_LB_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `CODEX_LB_OTEL_EXPORTER_ENDPOINT` | *(empty)* | OTLP exporter endpoint |

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_LEADER_ELECTION_ENABLED` | `false` | Leader election for singleton scheduler tasks |
| `CODEX_LB_LEADER_ELECTION_TTL_SECONDS` | `30` | Leader lease TTL |
| `CODEX_LB_CIRCUIT_BREAKER_ENABLED` | `false` | Per-account circuit breaker (**disabled** in production configmap — see note) |
| `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Failures before opening breaker |
| `CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `60` | Half-open retry interval |

> **Circuit breaker is disabled** in the production ConfigMap by default. The breaker is process-global: when open it causes `/health/ready` to return 503, removing the pod from Service endpoints. Enable only after validating the failure threshold and recovery timeout for your workload.

---

## Observability Setup

### Prometheus Metrics

```bash
# Enable in configmap.yaml:
CODEX_LB_METRICS_ENABLED: "true"

# Then apply the metrics service and ServiceMonitor:
kubectl apply -f deploy/k8s/service-metrics.yaml
kubectl apply -f deploy/k8s/servicemonitor.yaml
```

The `prometheus-client` package is installed in the production image (included via `--extra metrics`).

### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `codex_lb_requests_total` | Counter | Total proxied requests by status |
| `codex_lb_request_duration_seconds` | Histogram | Request latency distribution |
| `codex_lb_concurrent_requests` | Gauge | Current in-flight requests |
| `codex_lb_circuit_breaker_state` | Gauge | Per-account circuit breaker state (0=closed, 1=open, 2=half-open) |
| `codex_lb_active_accounts` | Gauge | Number of active upstream accounts |

### Structured Logging (JSON)

```yaml
# In configmap.yaml:
CODEX_LB_LOG_FORMAT: "json"
```

The container starts via `python -m app.cli`, which routes through `build_log_config()` and respects `CODEX_LB_LOG_FORMAT`.

### OpenTelemetry Tracing

The `opentelemetry-*` packages are installed in the production image (included via `--extra tracing`). To enable:

```yaml
CODEX_LB_OTEL_ENABLED: "true"
CODEX_LB_OTEL_EXPORTER_ENDPOINT: "http://otel-collector:4318"
```

---

## Resilience Features

| Feature | Status | Notes |
|---------|--------|-------|
| Readiness/liveness/startup probes | **Active** | Configured in Deployment |
| Graceful shutdown (drain in-flight) | **Active** | `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS=30` + preStop 15s |
| DB-backed rate limiter | **Active** | Cross-replica TOTP/password brute-force protection |
| Rolling update (maxUnavailable: 0) | **Active** | Zero-downtime deploys |
| PodDisruptionBudget (maxUnavailable: 1) | **Active** | HA safety during cluster maintenance |
| Leader election (singleton scheduler) | Opt-in | Enable `CODEX_LB_LEADER_ELECTION_ENABLED: "true"` for multi-replica |
| Circuit breaker | Disabled | Enable after threshold tuning; opens breaker → 503 on `/health/ready` |
| Prometheus metrics | Opt-in | `CODEX_LB_METRICS_ENABLED: "true"` |
| OpenTelemetry tracing | Opt-in | `CODEX_LB_OTEL_ENABLED: "true"` |

---

## Health Endpoints

| Probe | Path | K8s Purpose | Returns 503 When |
|-------|------|-------------|-----------------|
| Startup | `/health/startup` | Block traffic until initialized | App not yet ready |
| Readiness | `/health/ready` | Remove from Service endpoints | DB unreachable, draining, or circuit breaker OPEN |
| Liveness | `/health/live` | Restart unresponsive pods | Never (always 200) |

---

## Scaling

The HPA scales based on CPU with these defaults:

- **Min replicas**: 2 (HA)
- **Max replicas**: 10
- **Target CPU**: 70%

With multiple replicas, enable leader election to prevent duplicate background tasks:

```yaml
CODEX_LB_LEADER_ELECTION_ENABLED: "true"
```

---

## Session Bridge (Multi-Replica Routing)

Codex turn-state (`previous_response_id`) is managed by HTTP bridge sessions that are **per-process**. In a multi-replica deployment, a request with `previous_response_id` may hit a different pod than the one that owns that session, causing a 409 session-not-found error.

### How the instance ring works

Set `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING` in the ConfigMap to control which pod owns each session:

- **Empty ring (default)**: Single-instance mode — each pod routes all sessions to itself. The normalization function (`service.py:5087-5088`) auto-appends the pod's own hostname to the ring, so `_http_bridge_owner_instance` always returns `self`. **Safe for single-replica, causes session misses in multi-replica.**
- **Populated ring**: Set a comma-separated list of pod hostnames for consistent-hash routing, so each `previous_response_id` maps to a stable owner pod.

> **Note**: The current `deployment.yaml` uses a Deployment (not StatefulSet), so pod hostnames are not stable across restarts by default. A pod named `codex-lb-abc123` will get a different suffix after rescheduling.

### Multi-replica options

Choose one of these approaches when running more than one replica:

1. **Switch to StatefulSet** — gives stable pod names (e.g., `codex-lb-0`, `codex-lb-1`, `codex-lb-2`). Populate the ring:
   ```yaml
   CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING: "codex-lb-0,codex-lb-1,codex-lb-2"
   ```

2. **Sticky sessions at ingress/LB level** — configure your ingress controller so the same client always hits the same pod. Examples:
   - Nginx: `upstream hash $remote_addr consistent;`
   - Kubernetes `Service`: `sessionAffinity: ClientIP`
   This requires no ring configuration.

3. **Accept occasional session misses** — the proxy handles 409 errors gracefully (client can retry). Suitable for low-volume deployments where session affinity is not critical.

> **Validator note**: `settings.py:242-249` requires the instance ID to be present in the ring if the ring is non-empty. Do not set a ring that omits the current pod's hostname.

---

## Pre-Deployment Validation

Before applying manifests to your cluster, run the validation script to catch common misconfigurations:

```bash
bash scripts/validate-deploy.sh
```

The script checks (from the project root directory):

| Check | Pass | Fail |
|-------|------|------|
| All manifest files present | ✅ All 9 files found | ❌ Missing file |
| YAML syntax valid | ✅ All manifests parse | ❌ Parse error |
| No `:latest` tag | ✅ No latest tag | ❌ `:latest` found |
| `__IMAGE_TAG__` placeholder | ⚠️ WARN (expected in source) | — |
| `DATABASE_URL` not in configmap | ✅ Not in configmap | ❌ Found in configmap |
| Circuit breaker status | ✅ Disabled (safe default) | ⚠️ WARN if enabled |

Exit code 0 = all checks passed. Exit code 1 = one or more FAILs.

> The `__IMAGE_TAG__` check always produces a WARN — that placeholder is intentional in the source template. Substitute it with `sed` before applying to the cluster (see Step 4 of the rollout sequence above).

---

## Known Limitations

1. **WebSocket drain timeout**: The preStop hook sleeps 15 s and `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS=30` drains HTTP. Active WebSocket connections may be abruptly closed at `terminationGracePeriodSeconds=60`.

2. **Per-pod backpressure**: `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` is per-process. Effective cluster-wide limit = setting × replica count. Use the HPA to keep per-pod load in check.

3. **Circuit breaker scope**: The circuit breaker is process-global (one breaker for all upstream accounts). It trips on consecutive failures and opens `/health/ready` → K8s removes the pod from the Service. Keep disabled until you have tuned thresholds.

4. **Encryption key rotation**: There is no automated re-encryption. Key rotation requires a manual process and re-authentication of all accounts.

---

## Troubleshooting

### Pods stuck in CrashLoopBackOff

```bash
kubectl logs -l app=codex-lb --tail=50
```

Common causes:
- PostgreSQL unreachable — verify `CODEX_LB_DATABASE_URL` in the Secret and network reachability.
- Migration not run — run `job-migrate.yaml` before deploying.
- Encryption key not mounted — verify the Secret exists and the volume mount is present in the pod spec.

### Pods not becoming Ready

The readiness probe hits `/health/ready`. Failures mean:
- DB unreachable → 503
- Service is draining → 503
- Circuit breaker OPEN → 503 (check if circuit breaker is unexpectedly enabled)

```bash
kubectl describe pod -l app=codex-lb
# Look at Events and the readiness probe failure reason
```

The startup probe allows 60 s (30 × 2 s) for initialization. For slow DB migrations, increase `failureThreshold`.

### Migration Job failed

```bash
kubectl logs job/codex-lb-migrate
kubectl describe job codex-lb-migrate
```

Common causes:
- DB not yet available — ensure PostgreSQL is running before applying the Job.
- Wrong DB URL in Secret — update the Secret and re-create the Job.

To re-run a failed Job:
```bash
kubectl delete job codex-lb-migrate
sed "s/__IMAGE_TAG__/${IMAGE_TAG}/g" deploy/k8s/job-migrate.yaml | kubectl apply -f -
kubectl wait --for=condition=complete job/codex-lb-migrate --timeout=300s
```

### HPA not scaling

```bash
kubectl top pods -l app=codex-lb
```

If `kubectl top` returns "metrics not available", install [metrics-server](https://github.com/kubernetes-sigs/metrics-server).

### Graceful shutdown taking too long

The preStop sleep (15 s) + drain timeout (30 s) = 45 s total, within `terminationGracePeriodSeconds=60`. If requests are still dropped during rolling updates:

```bash
kubectl patch deployment codex-lb --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/terminationGracePeriodSeconds","value":90}]'
```

Increase `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` in the ConfigMap proportionally.

### Connection refused on port 2455

```bash
kubectl get endpoints codex-lb
```

If endpoints are empty, no pods are passing the readiness probe. Check pod logs and `kubectl describe pod`.
