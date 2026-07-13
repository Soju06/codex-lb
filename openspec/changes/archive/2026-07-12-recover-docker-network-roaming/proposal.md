## Why

Docker containers started on the default bridge can retain a Wi-Fi-provided DNS server across a host network change, leaving codex-lb unable to resolve upstream hosts until the container is restarted. codex-lb currently compounds that host-wide outage by treating each failed connection as an account failure, exhausting account retries and breaking continuity-sensitive Codex sessions.

## What Changes

- Make the portable Docker quick start use a user-defined bridge instead of the legacy default bridge, while documenting that Docker's embedded resolver can retain stale external forwarders on some Linux hosts.
- Add a Linux network-switching launch option that uses host networking with a stable host resolver, documents the direct-DHCP limitation, and does not hard-code a public DNS server.
- Classify DNS and local route failures as process-wide outbound-network failures, rotate stale shared HTTP client state, and keep those failures neutral to account health.
- Transparently retry proven pre-dispatch Responses work, token refresh, and upstream WebSocket connection attempts on the continuity owner within the existing request budget, allowing brief host network transitions to recover without a container restart.
- Add low-cardinality recovery diagnostics and an operator runbook for distinguishing host DNS failures from upstream/account failures.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-networking`: Stock Docker guidance distinguishes portable bridge networking from a Linux host-network option that uses a stable host resolver path.
- `outbound-http-clients`: Process-wide DNS/route failures rotate shared client state without penalizing individual accounts.
- `responses-api-compat`: Pre-visible stream and WebSocket connection attempts remain retryable on the continuity owner during a bounded local-network outage.
- `proxy-runtime-observability`: Network recovery attempts emit low-cardinality diagnostics without host resolver addresses or request payloads.

## Impact

- Affected runtime code: shared outbound HTTP client lifecycle, Responses streaming retry classification, and upstream WebSocket connection budgeting.
- Affected deployment surfaces: README Docker launch examples and Compose network contracts/tests.
- No API schema or database migration is required. Existing request budgets remain the upper bound for recovery waits.
