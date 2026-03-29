# Kubernetes Deployment Guide

Production deployment manifests for codex-lb on Kubernetes.

## Prerequisites

- Kubernetes 1.25+
- `kubectl` configured for your cluster
- PostgreSQL instance accessible from the cluster (required for multi-replica)
- (Optional) Prometheus Operator for metrics collection via ServiceMonitor

## Quick Start

```bash
# 1. Edit the ConfigMap with your database URL and settings
kubectl edit -f deploy/k8s/configmap.yaml

# 2. Apply all core manifests
kubectl apply -f deploy/k8s/

# 3. Verify pods are running
kubectl get pods -l app=codex-lb

# 4. Check readiness
kubectl rollout status deployment/codex-lb
```

If the Prometheus Operator CRD isn't installed, `kubectl apply -f deploy/k8s/` will warn about the ServiceMonitor. That's fine. Apply the five core files individually instead:

```bash
kubectl apply \
  -f deploy/k8s/configmap.yaml \
  -f deploy/k8s/deployment.yaml \
  -f deploy/k8s/service.yaml \
  -f deploy/k8s/service-metrics.yaml \
  -f deploy/k8s/hpa.yaml
```

## Manifests

| File | Kind | Purpose |
|------|------|---------|
| `deployment.yaml` | Deployment | 3 replicas with health probes, preStop hook, resource limits |
| `service.yaml` | Service (ClusterIP) | Exposes port 2455 for HTTP traffic |
| `configmap.yaml` | ConfigMap | All `CODEX_LB_` environment variables |
| `hpa.yaml` | HorizontalPodAutoscaler | Scales 2-10 replicas at 70% CPU |
| `service-metrics.yaml` | Service (ClusterIP) | Exposes port 9090 for Prometheus scraping |
| `servicemonitor.yaml` | ServiceMonitor | Prometheus Operator CRD for auto-discovery |

## Configuration Reference

Edit `configmap.yaml` before deploying. All values are strings.

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_DATABASE_URL` | (required) | PostgreSQL connection string. Format: `postgresql+asyncpg://user:pass@host:5432/dbname` |

> **Sensitive values**: For production, store `CODEX_LB_DATABASE_URL` in a Kubernetes Secret instead of the ConfigMap. Reference it via `secretKeyRef` in the Deployment's `env` block.

### Networking

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_HTTP_CONNECTOR_LIMIT` | `100` | Max total outbound HTTP connections |
| `CODEX_LB_HTTP_CONNECTOR_LIMIT_PER_HOST` | `50` | Max outbound connections per upstream host |
| `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` | `0` (unlimited) | Max concurrent inbound requests before rejecting with 503 |
| `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `30` | Seconds to drain in-flight requests during shutdown |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_METRICS_ENABLED` | `false` | Enable Prometheus metrics endpoint |
| `CODEX_LB_METRICS_PORT` | `9090` | Port for `/metrics` endpoint |
| `CODEX_LB_LOG_FORMAT` | `text` | Log format: `text` or `json` |
| `CODEX_LB_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `CODEX_LB_OTEL_EXPORTER_ENDPOINT` | (empty) | OTLP exporter endpoint |

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_LB_LEADER_ELECTION_ENABLED` | `false` | Enable leader election for singleton tasks (usage refresh, cleanup) |
| `CODEX_LB_LEADER_ELECTION_TTL_SECONDS` | `30` | Leader lease TTL |
| `CODEX_LB_CIRCUIT_BREAKER_ENABLED` | `false` | Enable per-account circuit breaker |
| `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive failures before opening circuit |
| `CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `60` | Seconds before half-open retry |

## Scaling

The HPA scales based on CPU utilization with these defaults:

- **Min replicas**: 2 (high availability)
- **Max replicas**: 10
- **Target CPU**: 70%

To adjust, edit `hpa.yaml` or patch:

```bash
kubectl patch hpa codex-lb-hpa -p '{"spec":{"maxReplicas":20}}'
```

For custom metrics scaling (e.g., concurrent requests), add a `metrics` entry targeting the Prometheus adapter:

```yaml
- type: Pods
  pods:
    metric:
      name: codex_lb_concurrent_requests
    target:
      type: AverageValue
      averageValue: "200"
```

### Leader Election

With multiple replicas, enable leader election so singleton background tasks (usage refresh, session cleanup) run on exactly one pod:

```yaml
CODEX_LB_LEADER_ELECTION_ENABLED: "true"
```

This uses the PostgreSQL database for coordination. No additional infrastructure needed.

## Monitoring

### Prometheus

1. Deploy the [Prometheus Operator](https://github.com/prometheus-operator/prometheus-operator) (or kube-prometheus-stack).
2. Apply the ServiceMonitor:
   ```bash
   kubectl apply -f deploy/k8s/servicemonitor.yaml
   ```
3. Prometheus auto-discovers codex-lb pods and scrapes `/metrics` on port 9090 every 30s.

### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `codex_lb_requests_total` | Counter | Total proxied requests by status |
| `codex_lb_request_duration_seconds` | Histogram | Request latency distribution |
| `codex_lb_concurrent_requests` | Gauge | Current in-flight requests |
| `codex_lb_circuit_breaker_state` | Gauge | Per-account circuit breaker state (0=closed, 1=open, 2=half-open) |
| `codex_lb_active_accounts` | Gauge | Number of active upstream accounts |

### Health Endpoints

The Deployment configures three probes:

| Probe | Path | Purpose |
|-------|------|---------|
| Startup | `/health/startup` | Blocks traffic until the app initializes (DB migrations, etc.) |
| Readiness | `/health/ready` | Removes pod from Service endpoints when unhealthy |
| Liveness | `/health/live` | Restarts pod if it becomes unresponsive |

## Secrets

The ConfigMap includes a placeholder `CODEX_LB_DATABASE_URL`. For production, move sensitive values to a Secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: codex-lb-secrets
type: Opaque
stringData:
  CODEX_LB_DATABASE_URL: "postgresql+asyncpg://user:password@postgres:5432/codexlb"
  CODEX_LB_ENCRYPTION_KEY_FILE: "/var/lib/codex-lb/encryption.key"
```

Then reference it in the Deployment:

```yaml
envFrom:
- configMapRef:
    name: codex-lb-config
- secretRef:
    name: codex-lb-secrets
```

Remove `CODEX_LB_DATABASE_URL` from the ConfigMap to avoid conflicts.

## Troubleshooting

### Pods stuck in CrashLoopBackOff

Check logs for database connection errors:

```bash
kubectl logs -l app=codex-lb --tail=50
```

Common causes:
- PostgreSQL isn't reachable from the cluster. Verify the connection string and network policies.
- Database doesn't exist yet. Create it before deploying.

### Pods not becoming Ready

The readiness probe hits `/health/ready`. If it fails:

```bash
kubectl describe pod -l app=codex-lb
```

Look at the Events section. The startup probe allows up to 60s (30 attempts x 2s) for initialization. If your database migrations take longer, increase `failureThreshold` in the startup probe.

### HPA not scaling

Verify metrics-server is installed:

```bash
kubectl top pods -l app=codex-lb
```

If `kubectl top` returns "metrics not available", install [metrics-server](https://github.com/kubernetes-sigs/metrics-server).

### Connection refused on port 2455

Confirm the Service is routing correctly:

```bash
kubectl get endpoints codex-lb
```

If endpoints are empty, no pods are passing the readiness probe. Check pod logs.

### Graceful shutdown taking too long

The preStop hook sleeps 15s to let the Service remove the pod from endpoints before draining. Combined with `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS=30`, the total shutdown window is ~45s (within the 60s `terminationGracePeriodSeconds`).

If requests are still being dropped during rolling updates, increase `terminationGracePeriodSeconds` and the preStop sleep proportionally.
