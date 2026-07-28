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
post-drain process cleanup.

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

## Full chart reference

For external database, production config, ingress, observability, and more see the
[Helm chart README](https://github.com/Soju06/codex-lb/blob/main/deploy/helm/codex-lb/README.md).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations)*
