## Why

Recent concurrency and chart changes still leave several fail-open or stale-state paths in place. Websocket half-open probes can admit more than one upstream session, account selection can return a stale account after concurrent deactivation or quota changes, HTTP bridge drain rejects reuse of already-live sessions, and the Helm chart still has unsafe defaults around external database wiring, install-time migrations, and namespace-wide ingress exposure.

## What Changes

- Hold websocket half-open probes for the full upstream lifecycle and close rejected aiohttp request context managers when the account circuit breaker is already open.
- Revalidate selected accounts after concurrent runtime mutations before returning them to callers.
- Allow reuse of live HTTP bridge sessions during drain while still refusing creation of new sessions.
- Harden Helm database wiring for external PostgreSQL, run chart-managed install migrations before app pods start, and fail closed on empty namespace ingress allowlists.

## Capabilities

### Modified Capabilities

- `responses-api-compat`
- `database-backends`
- `database-migrations`

### New Capabilities

- `deployment-networking`
