## Why

Recent rollout and transport changes introduced multiple fail-open or race-prone paths. Fresh Helm installs can start migrations from multiple pods or run the migration Job before its ConfigMap and Secret exist, bridge-enabled replicas can stay ready after bridge metadata lookup failures, websocket circuit breakers can miss post-handshake transport failures, and dashboard password login can now spend rate-limit budget even when no password is configured.

## What Changes

- Move the Helm migration flow to a single-writer path that works on fresh installs and with ExternalSecrets.
- Make startup fail closed when application-side startup migrations are disabled but the schema is still behind.
- Fail readiness when bridge ring metadata cannot be read while bridge routing is enabled.
- Track websocket circuit-breaker success/failure across the full upstream websocket request lifecycle instead of only the handshake.
- Exclude long-lived websocket connections from HTTP shutdown drain accounting.
- Preserve dashboard password login rate-limit budget when password auth is not configured.
- Make deployment and compose health checks react to effective readiness instead of stale environment references or liveness-only probes.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `database-migrations`: tighten startup and Helm migration behavior so only a single migration writer runs and app startup fails closed when schema is still behind.
- `responses-api-compat`: fail closed for bridge-enabled readiness lookup errors and count websocket upstream health over the full request lifecycle.
- `admin-auth`: keep password login rate limiting scoped to actual credential failures rather than unconfigured-password bootstrap requests.

## Impact

- Code: `app/db/session.py`, `app/modules/health/api.py`, `app/core/clients/proxy.py`, `app/main.py`, `app/modules/dashboard_auth/api.py`
- Deployment: `deploy/helm/codex-lb/templates/configmap.yaml`, `deploy/helm/codex-lb/templates/hooks/migration-job.yaml`, `deploy/helm/codex-lb/templates/deployment.yaml`, `docker-compose.prod.yml`
- Tests: Helm rendering tests, health probe tests, websocket proxy tests, shutdown tests, dashboard auth tests
