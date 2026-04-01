# codex-lb Helm Chart

Production-ready Helm chart for [codex-lb](https://github.com/soju06/codex-lb), an OpenAI API load balancer with account pooling, usage tracking, and dashboard.

## Design Goal

This chart is organized around **install modes**, not cloud vendors.

The same chart should work on Docker Desktop, kind, EKS, GKE, OKE, and other Kubernetes distributions. Cluster-specific concerns such as storage classes, ingress classes, load balancer annotations, and secret backends are expressed through values, while the application install contract stays the same.

## Prerequisites

- Helm 3.7+
- Kubernetes 1.25+
- Optional:
  - Prometheus Operator for `ServiceMonitor` and `PrometheusRule`
  - cert-manager for automated ingress TLS
  - Gateway API CRDs for `HTTPRoute`
  - External Secrets Operator for `externalSecrets.enabled=true`

## Install Modes

### 1. Bundled

Use the bundled Bitnami PostgreSQL sub-chart. This is the easiest self-contained install mode for demos, development clusters, and disposable environments.

Key properties:

- `postgresql.enabled=true`
- migration Job runs as a hook
- application pods include a schema gate initContainer and do not start the main container until Alembic head is visible

Example:

```bash
helm dependency build deploy/helm/codex-lb/

helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-bundled.yaml \
  --set postgresql.auth.password=change-me
```

### 2. External DB

Use an already reachable PostgreSQL database. This is the preferred production contract when the database is managed separately.

Key properties:

- `postgresql.enabled=false`
- direct DB URL or DB secret is available at install time
- migration Job runs `pre-install,pre-upgrade`
- application pods still keep the schema gate initContainer enabled

Supported DB wiring:

- `externalDatabase.url`
- `externalDatabase.host`, `externalDatabase.port`, `externalDatabase.database`, `externalDatabase.user`
- `externalDatabase.existingSecret`
- `auth.existingSecret` if one secret contains both `database-url` and `encryption-key`

Example using a direct URL:

```bash
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-db.yaml \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

Example using separate secrets:

```bash
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-db.yaml \
  --set externalDatabase.existingSecret=codex-lb-db \
  --set auth.existingSecret=codex-lb-app
```

### 3. External Secrets

Use External Secrets Operator to materialize credentials.

Key properties:

- `externalSecrets.enabled=true`
- DB credentials are not assumed to exist at render time
- migration Job remains `post-install,pre-upgrade`
- application pods keep the schema gate initContainer enabled and wait for schema head before starting the app container

Example:

```bash
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-secrets.yaml \
  --set externalSecrets.secretStoreRef.name=my-store
```

## Quick Start

### Docker Desktop / kind style cluster

Bundled PostgreSQL:

```bash
helm dependency build deploy/helm/codex-lb/
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-bundled.yaml \
  --set postgresql.auth.password=local-dev-password
```

### Managed PostgreSQL

```bash
helm dependency build deploy/helm/codex-lb/
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-db.yaml \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

## Included Value Overlays

Mode-centric overlays:

- `values-bundled.yaml`
- `values-external-db.yaml`
- `values-external-secrets.yaml`

Environment-oriented overlays kept for convenience:

- `values-dev.yaml`
- `values-staging.yaml`
- `values-prod.yaml`

The mode overlays define the installation contract. The environment overlays tune scale, observability, and routing posture.

## Schema and Migration Behavior

This chart intentionally uses a **single migration writer**.

- When startup migrations are disabled in the app pods, the chart relies on the dedicated migration Job to advance schema.
- Application pods use a schema gate initContainer when `migration.enabled=true`, `config.databaseMigrateOnStartup=false`, and `migration.schemaGate.enabled=true`.
- That initContainer runs `python -m app.db.migrate wait-for-head` and blocks the app container until the database is at Alembic head.

This means:

- bundled PostgreSQL installs do not need app pods to race migrations
- external DB installs with direct credentials can migrate before Deployment creation
- external secrets installs fail closed instead of serving on a stale schema

## Secret Model

The chart supports two secret patterns.

### Single secret

Use `auth.existingSecret` when one secret contains both:

- `database-url`
- `encryption-key`

### Split secrets

Use `externalDatabase.existingSecret` for the database URL and let the chart manage or reference a separate app secret for `encryption-key`.

When `externalDatabase.existingSecret` is set and `auth.existingSecret` is not, the chart-managed app secret contains only the encryption key; the Deployment reads `CODEX_LB_DATABASE_URL` from the external DB secret.

## Network Policy

When `networkPolicy.enabled=true`, the chart now fails closed for the main HTTP ingress port.

- The chart does **not** open port `2455` to every namespace by default.
- To allow ingress-controller traffic, set `networkPolicy.ingressNSMatchLabels`.
- For custom cases, use `networkPolicy.extraIngress`.

Example:

```yaml
networkPolicy:
  enabled: true
  ingressNSMatchLabels:
    kubernetes.io/metadata.name: ingress-nginx
```

## Connection Pool Sizing

Each pod keeps its own SQLAlchemy pool.

```
total_connections = (databasePoolSize + databaseMaxOverflow) × replicas
```

Keep this within your PostgreSQL `max_connections` budget or place PgBouncer in front of the database.

## Security

The chart targets the Kubernetes Restricted Pod Security Standard.

- `runAsNonRoot: true`
- `readOnlyRootFilesystem: true`
- `allowPrivilegeEscalation: false`
- all Linux capabilities dropped
- `automountServiceAccountToken: false`

Rollout controls for externally managed config:

- `rollout.reloader.enabled=true` adds Stakater Reloader annotations
- `rollout.manualToken` forces a Deployment rollout when external Secret contents change outside Helm

## Ingress and Gateway API

The chart supports either classic Ingress or Gateway API.

Ingress example:

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  hosts:
    - host: codex-lb.example.com
      paths:
        - path: /
          pathType: Prefix
```

Gateway API example:

```yaml
gatewayApi:
  enabled: true
  parentRefs:
    - name: my-gateway
      namespace: gateway-system
  hostnames:
    - codex-lb.example.com
```

## Upgrade Contract

```bash
helm upgrade codex-lb deploy/helm/codex-lb/ <your values...>
```

- The migration Job stays the only automatic schema writer.
- App pods wait for Alembic head before the main container starts when the schema gate is enabled.
- Deployment checksums force rollouts when chart-managed ConfigMaps or Secrets change.

## Validation

Recommended after install:

```bash
helm test codex-lb -n <namespace>
kubectl get pods -n <namespace>
kubectl logs job/<release>-migrate -n <namespace>
```

If you are using a port-forwarded install:

```bash
kubectl port-forward svc/codex-lb 2455:2455 -n <namespace>
curl -i http://127.0.0.1:2455/health/live
curl -i http://127.0.0.1:2455/health/ready
```

## Troubleshooting

Migration Job:

```bash
kubectl describe job <release>-migrate -n <namespace>
kubectl logs job/<release>-migrate -n <namespace>
```

App pod stuck in init:

```bash
kubectl describe pod -l app.kubernetes.io/name=codex-lb -n <namespace>
kubectl logs deploy/<release> -c wait-for-schema-head -n <namespace>
```

Health failures:

```bash
kubectl describe deploy <release> -n <namespace>
kubectl logs deploy/<release> -n <namespace>
```
