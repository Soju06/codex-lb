## Context

This change crosses deployment templates, startup behavior, readiness, websocket transport health tracking, shutdown flow, and dashboard auth. The main operational risk is fail-open behavior: pods can become ready before schema prerequisites exist, bridge-enabled replicas can keep serving after ring metadata failures, and websocket upstream failures can evade the account circuit breaker after a successful `101` handshake.

## Goals / Non-Goals

**Goals:**
- Ensure schema changes use a single-writer migration path in Helm installs and upgrades.
- Prevent application pods from serving when startup migrations are disabled but schema is not current.
- Drain broken bridge-enabled replicas out of service quickly.
- Count websocket upstream health over the full request lifecycle.
- Avoid shutdown stalls caused by long-lived websocket sessions.
- Preserve admin bootstrap/login ergonomics by spending rate-limit budget only on real password checks.

**Non-Goals:**
- Introduce a new leader-election-based migration coordinator.
- Add new external dependencies or controllers.
- Redesign websocket shutdown semantics beyond removing them from HTTP drain accounting.

## Decisions

### Use a hook-backed migration Job as the single writer

The chart will stop auto-enabling `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP` for fresh installs with ExternalSecrets. Instead, migrations stay on the dedicated Job path, and the hook timing moves away from `pre-install` so the Job starts only after chart-managed ConfigMaps, Secrets, and ExternalSecrets are created.

Alternative considered: keep startup migrations enabled only for replica `0`.
Rejected because Deployments do not provide a safe single-writer guarantee and concurrent pod starts can still race.

### Fail startup closed when migrations are disabled but schema is behind

When `database_migrate_on_startup=false`, startup will inspect migration state and fail when Alembic is not already at `head`. That lets the post-install hook remain the single writer without allowing app pods to become ready against a stale schema.

Alternative considered: let readiness detect schema lag later.
Rejected because the process would still start background tasks and can serve partial traffic before readiness flips.

### Treat bridge ring lookup errors as readiness failures

If bridge routing is enabled and bridge metadata lookup errors, readiness should return `503` just like a non-member replica does. This matches the bridge routing contract, which already fails closed later in request handling.

### Record websocket circuit-breaker health at lifecycle completion

Handshake success alone is not enough to prove upstream health. The circuit breaker will record success only after a terminal websocket response event is seen, and it will record failure when the upstream websocket errors or closes before a terminal event.

### Keep shutdown drain scoped to HTTP requests

The drain counter exists to wait for finite HTTP request lifetimes before shutdown. Long-lived websocket sessions are not currently closed by that path, so counting them only adds fixed timeout delays without graceful benefit. The in-flight middleware will therefore track only HTTP scopes.

### Trigger Deployment rollouts from rendered config references

The Deployment pod template will carry rendered checksums for ConfigMap and Secret-backed configuration so `helm upgrade` rolls pods when chart-managed env inputs change.

For Secret data that changes outside Helm, the chart will also expose an operator-controlled rollout signal: optional Stakater Reloader annotations and a manual rollout token. That keeps external Secret rotation workable without pretending Helm can hash data it does not render.

## Risks / Trade-offs

- [Risk] Post-install migrations can delay first readiness on fresh installs. → Mitigation: app startup fails closed until schema reaches head, which is safer than serving against a stale schema.
- [Risk] Helm cannot observe out-of-band mutations to an existing external Secret. → Mitigation: checksum rendered chart-managed resources and ExternalSecret specs, plus offer optional Reloader annotations and a manual rollout token for operator-managed secret rotations.
- [Risk] Treating bridge metadata errors as readiness failures can temporarily reduce capacity during transient DB issues. → Mitigation: this matches existing fail-closed request behavior and prevents broken replicas from serving continuation traffic.

## Migration Plan

1. Render the updated chart so fresh installs use post-install/pre-upgrade migrations and rollout checksums.
2. Deploy the application changes so startup fails until schema is current when startup migrations are disabled.
3. Verify readiness behavior, websocket circuit-breaker regression coverage, and dashboard login bootstrap behavior.

## Open Questions

- None.
