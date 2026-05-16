# codex-lb Helm Chart

Production-ready Helm chart for [codex-lb](https://github.com/soju06/codex-lb), an OpenAI API load balancer with account pooling, usage tracking, and dashboard.

## Design Goal

This chart is organized around **install modes**, not cloud vendors.

The same chart should work on Docker Desktop, kind, EKS, GKE, OKE, and other Kubernetes distributions. Cluster-specific concerns such as storage classes, ingress classes, load balancer annotations, and secret backends are expressed through values, while the application install contract stays the same.

## Prerequisites

- Helm 3.7+
- Kubernetes 1.32+
- Optional:
  - Prometheus Operator for `ServiceMonitor` and `PrometheusRule`
  - cert-manager for automated ingress TLS
  - Gateway API CRDs for `HTTPRoute`
  - External Secrets Operator for `externalSecrets.enabled=true`

## Version Policy

- Minimum supported Kubernetes version: `1.32`
- Validation baseline in CI and smoke installs: `1.35`

This is a project support policy. Cloud providers may keep older versions available for some time, but the chart and CI no longer optimize for pre-`1.32` clusters.

## Install Modes

### 1. Bundled

Use the bundled Bitnami PostgreSQL sub-chart. This is the easiest self-contained install mode for demos, development clusters, and disposable environments.

Key properties:

- `postgresql.enabled=true`
- `values-bundled.yaml` enables `databaseMigrateOnStartup=true`
- the migration Job is reserved for upgrades (`pre-upgrade`)
- fresh installs stay self-contained and single-replica friendly

Example:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=change-me \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
```

<details>
<summary>From source</summary>

```bash
helm dependency build deploy/helm/codex-lb/
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-bundled.yaml \
  --set postgresql.auth.password=change-me
```

</details>

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
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.enabled=false \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

Example using separate secrets:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.enabled=false \
  --set externalDatabase.existingSecret=codex-lb-db \
  --set auth.existingSecret=codex-lb-app
```

<details>
<summary>From source</summary>

```bash
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-db.yaml \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

</details>

### 3. External Secrets

Use External Secrets Operator to materialize credentials.

Key properties:

- `externalSecrets.enabled=true`
- DB credentials are not assumed to exist at render time
- migration Job remains `post-install,pre-upgrade`
- application pods keep the schema gate initContainer enabled and wait for schema head before starting the app container

Example:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.enabled=false \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStoreRef.name=my-store
```

<details>
<summary>From source</summary>

```bash
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-external-secrets.yaml \
  --set externalSecrets.secretStoreRef.name=my-store
```

</details>

## Quick Start

No repo clone required — install directly from the OCI registry.

### Docker Desktop / kind style cluster

Bundled PostgreSQL:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=local-dev-password \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
```

### Managed PostgreSQL

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.enabled=false \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

### From source (development)

If you need to customize the chart itself, clone the repo and install from path:

```bash
helm dependency build deploy/helm/codex-lb/
helm upgrade --install codex-lb deploy/helm/codex-lb/ \
  -f deploy/helm/codex-lb/values-bundled.yaml \
  --set postgresql.auth.password=local-dev-password
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

This chart intentionally keeps migration behavior explicit by install mode.

- In external DB and external secrets modes, the chart relies on the dedicated migration Job to advance schema.
- Application pods use a schema gate initContainer when `migration.enabled=true`, `config.databaseMigrateOnStartup=false`, and `migration.schemaGate.enabled=true`.
- That initContainer runs `python -m app.db.migrate wait-for-head` and blocks the app container until the database is at Alembic head.
- In bundled mode, `values-bundled.yaml` enables startup migration instead of the schema gate so fresh self-contained installs do not deadlock on `helm install --wait`.

This means:

- bundled PostgreSQL installs bootstrap themselves without requiring a separate install-time migration writer
- external DB installs with direct credentials can migrate before StatefulSet creation
- external secrets installs fail closed instead of serving on a stale schema

## Secret Model

The chart supports two secret patterns.

### Single secret

Use `auth.existingSecret` when one secret contains both:

- `database-url`
- `encryption-key`

### Split secrets

Use `externalDatabase.existingSecret` for the database URL and let the chart manage or reference a separate app secret for `encryption-key`.

When `externalDatabase.existingSecret` is set and `auth.existingSecret` is not, the chart-managed app secret contains only the encryption key; the StatefulSet reads `CODEX_LB_DATABASE_URL` from the external DB secret.

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

## Production Workload

Multi-replica production deployments require careful coordination of database connectivity, session routing, and graceful shutdown. This section covers the key patterns and tuning parameters.

### Prerequisites for Multi-Replica

Single-replica deployments can use SQLite, but **multi-replica requires PostgreSQL**:

- **Database**: PostgreSQL is mandatory for multi-replica because:
  - SQLite does not support concurrent writes from multiple pods
  - Leader election requires a shared database backend
  - Session bridge ring membership is stored in the database
  
- **Leader Election**: Enabled by default (`config.leaderElectionEnabled=true`)
  - Ensures only one pod performs background tasks (e.g., session cleanup, metrics aggregation)
  - Uses database-backed locking with a TTL (`config.leaderElectionTtlSeconds=30`)
  - If the leader crashes, another pod acquires the lock within 30 seconds
  
- **Circuit Breaker**: Enabled by default (`config.circuitBreakerEnabled=true`)
  - Protects upstream API endpoints from cascading failures
  - Opens after `config.circuitBreakerFailureThreshold=5` consecutive failures
  - Enters half-open state after `config.circuitBreakerRecoveryTimeoutSeconds=60` seconds
  - Prevents thundering herd when upstream is degraded

### Session Bridge Ring

The session bridge is an in-memory cache of upstream WebSocket connections, shared across the pod ring.

**Automatic Ring Membership (PostgreSQL)**

When using PostgreSQL, ring membership is **automatic and database-backed**:

- Each pod registers itself in the database on startup
- Each pod auto-advertises its owner-handoff endpoint via headless-service DNS
- The `sessionBridgeInstanceRing` field is **optional** and only needed for manual pod list override
- Pods discover each other via database queries; no manual configuration required
- Ring membership is cleaned up automatically when pods terminate

The chart configures each pod with:

- StatefulSet name: `<release>-codex-lb-workload`
- `serviceName: <release>-codex-lb-bridge` on the StatefulSet
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID=$(POD_NAME)`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL=http://$(POD_NAME).<headless-service>.$(POD_NAMESPACE).svc.<clusterDomain>:2455`

`clusterDomain` defaults to `cluster.local`. If your cluster uses another suffix, set:

```yaml
clusterDomain: corp.internal
```

In most clusters no extra values are required for `/responses` owner handoff. If pods must be reached through a different internal address, override:

```yaml
config:
  sessionBridgeAdvertiseBaseUrl: "http://codex-lb-internal.default.svc.cluster.local:2455"
```

When `networkPolicy.enabled=true`, the chart also allows port `2455` traffic between codex-lb pods so owner handoff can work without extra rules.

**Manual Ring Override (Advanced)**

If you need to manually specify the pod ring (e.g., for testing or debugging):

```yaml
config:
  sessionBridgeInstanceRing: "codex-lb-0.codex-lb.default.svc.cluster.local,codex-lb-1.codex-lb.default.svc.cluster.local"
```

This is rarely needed in production; the database-backed discovery is preferred.

### Connection Pool Budget

Each pod maintains its own SQLAlchemy connection pool. The total connections across all replicas must fit within PostgreSQL's `max_connections`:

```
(databasePoolSize + databaseMaxOverflow) × maxReplicas ≤ PostgreSQL max_connections
```

**Example for `values-prod.yaml`:**

```yaml
config:
  databasePoolSize: 3
  databaseMaxOverflow: 2
autoscaling:
  maxReplicas: 20
```

Calculation: `(3 + 2) × 20 = 100` connections, which fits within PostgreSQL's default `max_connections=100`.

**Tuning:**

- Increase `databasePoolSize` if pods frequently wait for connections
- Increase `databaseMaxOverflow` for temporary spikes, but keep it small (overflow is slower)
- Reduce `maxReplicas` if you cannot increase PostgreSQL's `max_connections`
- Use PgBouncer or pgcat as a connection pooler in front of PostgreSQL if needed

### values-prod.yaml Reference

The `values-prod.yaml` overlay is pre-configured for production multi-replica deployments:

```yaml
replicaCount: 3                    # Start with 3 replicas
postgresql:
  enabled: false                   # Use external PostgreSQL
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 20
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 600  # 10 min cooldown (see below)
affinity:
  podAntiAffinity: hard            # Spread pods across nodes
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone  # Spread across zones
networkPolicy:
  enabled: true                    # Restrict ingress/egress
metrics:
  serviceMonitor:
    enabled: true                  # Prometheus scraping
  prometheusRule:
    enabled: true                  # Alerting rules
  grafanaDashboard:
    enabled: true                  # Pre-built dashboards
externalSecrets:
  enabled: true                    # Use External Secrets Operator
```

Install with:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  -f deploy/helm/codex-lb/values-prod.yaml \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/codexlb'
```

### Graceful Shutdown Tuning

Graceful shutdown coordinates three timeout parameters to drain in-flight requests and session bridge connections:

```
preStopSleepSeconds (15s minimum) → shutdownDrainTimeoutSeconds (30s) → terminationGracePeriodSeconds (60s)
```

**Timeline:**

1. **preStopSleepSeconds (15s minimum)**: Pod enters preStop
   - Calls `/internal/drain/start` so readiness fails and new app requests are rejected
   - Polls `/internal/drain/status` during the wait so in-flight state is visible while draining
   - Gives Kubernetes/load balancers time to remove the pod from rotation before SIGTERM
   
2. **shutdownDrainTimeoutSeconds (30s)**: Drain in-flight requests
   - HTTP server stops accepting new connections
   - Any remaining requests are allowed to complete (up to 30 seconds)
   - Session bridge connections are gracefully closed
   
3. **terminationGracePeriodSeconds (60s)**: Hard deadline
   - Total time from SIGTERM to SIGKILL
   - Must be ≥ `preStopSleepSeconds + shutdownDrainTimeoutSeconds`
   - Default 60s allows 15s + 30s + 15s buffer

**Tuning:**

- Increase `preStopSleepSeconds` if your load balancer takes longer to deregister or short requests need a larger pre-SIGTERM drain window
- Increase `shutdownDrainTimeoutSeconds` if requests typically take >30s to complete
- Increase `terminationGracePeriodSeconds` proportionally (must be larger than the sum)
- Keep the buffer small; long shutdown times delay pod replacement

Example for long-running requests:

```yaml
preStopSleepSeconds: 20
shutdownDrainTimeoutSeconds: 60
terminationGracePeriodSeconds: 90
```

### Scale-Down Caution

The `stabilizationWindowSeconds: 600` (10 minutes) in `values-prod.yaml` is intentionally high.

**Why?**

- Session bridge connections have idle TTLs (`sessionBridgeIdleTtlSeconds=120` for API, `sessionBridgeCodexIdleTtlSeconds=900` for Codex)
- When a pod scales down, its in-memory sessions are lost
- Clients reconnecting to a different pod must re-establish upstream connections
- A 10-minute cooldown prevents rapid scale-down/up cycles that would thrash session state

**Behavior:**

- HPA will scale down at most 1 pod every 2 minutes (when cooldown is active)
- If load drops suddenly, scale-down is delayed by up to 10 minutes
- This trades off faster scale-down for session stability

**Tuning:**

- Reduce `stabilizationWindowSeconds` if you prioritize cost over session stability
- Increase it if you see frequent session reconnections during scale events
- Monitor `sessionBridgeInstanceRing` size changes in logs to detect scale-down impact

## Security

The chart targets the Kubernetes Restricted Pod Security Standard.

- `runAsNonRoot: true`
- `readOnlyRootFilesystem: true`
- `allowPrivilegeEscalation: false`
- all Linux capabilities dropped
- `automountServiceAccountToken: false`

Rollout controls for externally managed config:

- `rollout.reloader.enabled=true` adds Stakater Reloader annotations
- `rollout.manualToken` forces a StatefulSet rollout when external Secret contents change outside Helm

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
helm upgrade codex-lb oci://ghcr.io/soju06/charts/codex-lb <your values...>
```

- External DB installs can migrate before StatefulSet creation.
- External secrets installs keep the dedicated migration Job and fail closed behind the schema gate.
- Bundled installs stay easy to bootstrap and keep the migration hook for upgrades.
- StatefulSet pod-template checksums force rollouts when chart-managed ConfigMaps or Secrets change.
- The workload resource name is intentionally different from the legacy Deployment name to avoid Helm kind-migration conflicts during upgrade.

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
