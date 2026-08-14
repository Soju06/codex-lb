# Kubernetes

Install with Helm:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=changeme \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
kubectl port-forward svc/codex-lb 2455:2455
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

## Multi-replica behavior

The Helm chart auto-configures HTTP `/responses` owner handoff for multi-replica installs using a headless-service DNS name per pod. The default cluster domain is `cluster.local`; set Helm `clusterDomain` if your cluster uses a different suffix. Override `config.sessionBridgeAdvertiseBaseUrl` only if pods must be reached through a different internal address.

In multi-replica setups, replicas must share the same encryption key (the Helm chart default) for bootstrap-token restart recovery and encrypted-data access to work.

### Account concurrency caps are cluster-wide

Under the default `CODEX_LB_PROXY_ACCOUNT_CAPS_SCOPE=partitioned`, `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT` (default 8) and `CODEX_LB_PROXY_ACCOUNT_RESPONSE_CREATE_LIMIT` are cluster-wide targets. Each replica enforces its own deterministic share of a **positive** cap — `floor(cap / replicas)`, with the remainder distributed one slot at a time and every share floored at 1 — so with the default stream cap and three replicas, one account gets 3/3/2 slots per replica, not 8 each. A cap of `0` stays unlimited on every replica; it is never floored to one slot. Setting the scope to `replica` opts out of partitioning entirely: every replica then enforces the full configured cap.

Practical consequences:

- Size a positive cap for the total per-account concurrency you want across the cluster; adding replicas re-partitions it rather than raising it — except when the cap is smaller than the replica count, where the floor of 1 makes the aggregate equal the replica count and grow with each added replica. Disconnect-heavy or agent workloads typically want `~8 × replicas`.
- On an initialized deployment the caps live in **dashboard settings** (Settings → routing), which override the environment values — raising the env var and restarting pods changes nothing once the deployment is initialized. Change the cap from the dashboard; the environment values only seed the initial dashboard row.
- `CODEX_LB_PROXY_ACCOUNT_STREAM_RECOVERY_RESERVE` (default 1) is subtracted from each replica's share at selection time, so small shares feel it disproportionately: a share of 2 leaves 1 slot for new selection.
- Persistent `account_stream_cap` errors with idle replicas are the undersizing signature; raise the cap first.
- Run one process per pod (`workers_per_instance` stays 1): shares are partitioned across ring members, and worker processes inside one pod would silently multiply the share.

Semantics and sizing rationale: [proxy-admission-control](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-admission-control).

## Graceful shutdown

The chart's preStop hook commits one process drain deadline before Uvicorn
closes HTTP or WebSocket connections. It first allows the configured routing
dwell to elapse, then returns as soon as tracked work reaches zero; otherwise
it waits only until the same application deadline. A later SIGTERM reuses that
deadline instead of starting another drain period.

During that bounded window, new WebSocket connections and new Responses turns
are rejected. An admitted Responses turn may finish terminal delivery,
request-log persistence, and API-key settlement; an idle admitted connection
closes promptly. Kubernetes' `terminationGracePeriodSeconds` starts before the
preStop helper, so it must also reserve time for helper startup and bounded
post-drain process cleanup. If that cleanup ignores cancellation at its bound,
the launcher forces the captured signal (or SIGTERM for programmatic shutdown)
instead of returning an unbounded task to asyncio runner teardown.

**Upgrade warning:** this release adds a render-time timing guard. Existing
values files, `--set` overrides, or values retained by
`helm upgrade --reuse-values` with
`terminationGracePeriodSeconds < config.shutdownDrainTimeoutSeconds + 32`
make `helm template`, `helm install`, and `helm upgrade` fail before resources
are applied. With the default `config.shutdownDrainTimeoutSeconds: 30`, the
minimum is `62`; the chart default is `65`. Raise every retained low value
explicitly to at least the computed minimum (`65` preserves the chart's default
headroom for a 30-second drain). Omitting the key does not clear its stored
value when `--reuse-values` is used. To adopt the chart default instead, use an
intentional non-reuse or `--reset-values` upgrade with the key absent. Production
overrides should retain additional helper-launch headroom.

The defaults satisfy the chart's timing guards. When tuning them, keep
`preStopSleepSeconds <= config.shutdownDrainTimeoutSeconds` and
`terminationGracePeriodSeconds >= config.shutdownDrainTimeoutSeconds + 32`.
See the owning
[deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation)
contract and [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations)
operational context.

## Gateway API path filters

Set `gatewayApi.rules` when different request paths need different Gateway API
filters. The chart renders each rule's `matches` and `filters` in order and
adds the codex-lb Service backend automatically. For example, this keeps API
traffic direct while applying a Traefik forward-auth middleware to the
dashboard catch-all:

```yaml
gatewayApi:
  enabled: true
  parentRefs:
    - name: gateway
      namespace: gateway-system
  hostnames:
    - codex-lb.example.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1
        - path:
            type: PathPrefix
            value: /backend-api/codex
        - path:
            type: PathPrefix
            value: /backend-api/wham
        - path:
            type: PathPrefix
            value: /backend-api/transcribe
        - path:
            type: PathPrefix
            value: /backend-api/files
        - path:
            type: PathPrefix
            value: /api/codex
    - matches:
        - path:
            type: PathPrefix
            value: /
      filters:
        - type: ExtensionRef
          extensionRef:
            group: traefik.io
            kind: Middleware
            name: oauth-forward-auth
```

The default empty `rules` list preserves the chart's catch-all HTTPRoute.
Keep `/backend-api/wham`, `/backend-api/files`, and `/api/codex` in the
unfiltered API rule: WHAM identity discovery, file uploads, and Codex
usage/reset-credit calls authenticate independently of the dashboard's
forward-auth middleware.
Extension resources must be valid for the release namespace according to the
Gateway implementation.

## Application-specific Gateway

When no shared Gateway exists (or the release should not depend on one), set
`gatewayApi.gateway.create=true` to render a Gateway dedicated to this release
in the release namespace. The chart's HTTPRoute attaches to it automatically
and `gatewayApi.parentRefs` is ignored:

```yaml
gatewayApi:
  enabled: true
  gateway:
    create: true
    gatewayClassName: envoy
  hostnames:
    - codex-lb.example.com
```

`gatewayApi.gateway.gatewayClassName` is required when `create=true`. The
Gateway defaults to a single HTTP listener on port 80; override
`gatewayApi.gateway.listeners` for TLS or other ports.

## Grafana dashboard hierarchy

The chart can assign concise titles to its packaged dashboards without copying
their JSON. When the Grafana sidecar maps annotation paths to filesystem-backed
nested folders, the following values produce `Applications / Codex LB /
Overview` and `Applications / Codex LB / TTFT Breakdown`:

```yaml
metrics:
  grafanaDashboard:
    enabled: true
    folder: Applications/Codex LB
    titles:
      codex-lb.json: Overview
      ttft-breakdown.json: TTFT Breakdown
```

The title map is keyed by the JSON filenames packaged in the chart. Omitting it
preserves the default dashboard titles.

## Full chart reference

For external database, production config, ingress, observability, and more see the
[Helm chart README](https://github.com/Soju06/codex-lb/blob/main/deploy/helm/codex-lb/README.md).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations) · [proxy-admission-control](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-admission-control)*
