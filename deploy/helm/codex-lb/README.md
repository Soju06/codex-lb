# codex-lb Helm Chart

Production-grade Helm chart for [codex-lb](https://github.com/soju06/codex-lb), an OpenAI API load balancer with account pooling, usage tracking, and dashboard.

## Prerequisites

- Helm 3.7+
- Kubernetes 1.25+
- (Optional) Prometheus Operator, for `ServiceMonitor` and `PrometheusRule`
- (Optional) cert-manager, for automatic TLS certificate management
- (Optional) Gateway API CRDs, for `HTTPRoute` support
- (Optional) External Secrets Operator, for `ExternalSecret` integration

## Quick Start

```bash
# Add Bitnami repository (for PostgreSQL sub-chart)
helm dependency build deploy/helm/codex-lb/

# Install with bundled PostgreSQL (development)
helm install codex-lb deploy/helm/codex-lb/ \
  --set postgresql.auth.password=mypassword

# Install with external PostgreSQL (production)
helm install codex-lb deploy/helm/codex-lb/ \
  --set postgresql.enabled=false \
  --set auth.existingSecret=codex-lb-secrets
```

## Configuration

All configurable values are documented in [values.yaml](values.yaml) with `@param` annotations.

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| Deployment | `replicaCount` | `2` | Number of replicas |
| Deployment | `resources.requests.cpu` | `200m` | CPU request |
| Application | `config.logFormat` | `json` | Log format (`json` or `text`) |
| Metrics | `metrics.enabled` | `true` | Enable Prometheus metrics |
| HPA | `autoscaling.enabled` | `false` | Enable HorizontalPodAutoscaler |
| PDB | `pdb.create` | `true` | Enable PodDisruptionBudget |
| NetworkPolicy | `networkPolicy.enabled` | `false` | Enable NetworkPolicy |

Use environment overlays for multi-env deployments:

```bash
helm install codex-lb deploy/helm/codex-lb/ -f values-prod.yaml
```

## Database Setup

### Bundled PostgreSQL (development/staging)

```yaml
postgresql:
  enabled: true
  auth:
    username: codexlb
    password: changeme  # CHANGE THIS
    database: codexlb
```

### External PostgreSQL (production)

```yaml
postgresql:
  enabled: false
auth:
  existingSecret: my-db-secret  # must contain keys: database-url, encryption-key
```

Or provide via `externalDatabase.url`:

```yaml
postgresql:
  enabled: false
externalDatabase:
  url: "postgresql+asyncpg://user:pass@host:5432/codexlb"
```

## Connection Pool Sizing

When running multiple replicas, each pod maintains a pool of database connections. The total connections used is:

```
total_connections = (databasePoolSize + databaseMaxOverflow) × replicas
```

PostgreSQL defaults to `max_connections=100`. With 20 replicas:

| Pool Size | Max Overflow | Replicas | Total | Notes |
|-----------|-------------|----------|-------|-------|
| 3 | 2 | 20 | 100 | **Recommended prod default** |
| 5 | 5 | 10 | 100 | For ≤10 replicas |
| 15 | 10 | 4 | 100 | For single-instance only |

**If you need more concurrency**: Increase `max_connections` in PostgreSQL (requires restart), or deploy [PgBouncer](https://www.pgbouncer.org/) as a connection pooler.

## Security

This chart enforces the Kubernetes **Restricted** Pod Security Standard:

- `runAsNonRoot: true`, `runAsUser: 1000`
- `readOnlyRootFilesystem: true` (with emptyDir for `/tmp` and `/app/.cache`)
- `allowPrivilegeEscalation: false`
- All Linux capabilities dropped
- `automountServiceAccountToken: false`
- `seccompProfile: RuntimeDefault`

**Secret Management:**

- Chart-managed Secret: `stringData` with `database-url` and `encryption-key` keys
- ExternalSecrets Operator: set `externalSecrets.enabled: true` with `secretStoreRef`
- Bring-your-own: set `auth.existingSecret: my-secret`

**Rollout on external secret changes:**

- Chart-managed ConfigMap/Secret changes already trigger rollout checksums on `helm upgrade`
- If Secret data changes outside Helm, enable `rollout.reloader.enabled: true` when you run [Stakater Reloader](https://github.com/stakater/Reloader)
- If you do not use a reloader controller, bump `rollout.manualToken` to force a Deployment rollout after rotating an external Secret

Example:

```yaml
rollout:
  reloader:
    enabled: true
  manualToken: ""
```

Generate an encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Observability

Enable full observability stack:

```yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true  # requires Prometheus Operator
  prometheusRule:
    enabled: true  # 4 alert rules: HighErrorRate, HighLatency, PodDown, HPAAtMax
  grafanaDashboard:
    enabled: true  # auto-provisioned via ConfigMap sidecar
```

## Ingress & Gateway API

### Standard Ingress (nginx)

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  certManager:
    enabled: true
    clusterIssuer: letsencrypt-prod
  hosts:
    - host: codex-lb.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codex-lb-tls
      hosts:
        - codex-lb.example.com
```

### Gateway API

```yaml
gatewayApi:
  enabled: true
  parentRefs:
    - name: my-gateway
      namespace: gateway-system
  hostnames:
    - codex-lb.example.com
```

## Upgrading

Upgrades trigger the pre-upgrade migration Job automatically:

```bash
helm upgrade codex-lb deploy/helm/codex-lb/ -f values-prod.yaml
```

The migration Job runs `python -m app.db.migrate upgrade` before the new pods start. If the migration fails (up to 3 retries), the upgrade is halted and the failed Job is preserved for debugging.

Rolling updates use `maxSurge: 1, maxUnavailable: 0`, so new pods must pass health checks before old pods are terminated.

For externally managed Secret rotations, either rely on `rollout.reloader.enabled` or change `rollout.manualToken` during `helm upgrade` so the Deployment template changes and Kubernetes creates a new ReplicaSet.

## Uninstalling

```bash
helm uninstall codex-lb
# Manually delete the PersistentVolumeClaim if postgresql.primary.persistence.enabled=true:
kubectl delete pvc -l app.kubernetes.io/name=postgresql
```

## Troubleshooting

**Migration Job fails:**

```bash
kubectl describe job codex-lb-migrate
kubectl logs -l app.kubernetes.io/component=migration
```

**Health check failures:**

```bash
kubectl describe pod -l app.kubernetes.io/name=codex-lb
# Check probe: /health/startup (init), /health/ready (traffic), /health/live (alive)
```

**Secret errors (encryption key):**

```bash
kubectl get secret codex-lb -o jsonpath='{.data.encryption-key}' | base64 -d | wc -c
# Must be 44 bytes (Fernet key)
```

**Run Helm tests:**

```bash
helm test codex-lb -n your-namespace
```
