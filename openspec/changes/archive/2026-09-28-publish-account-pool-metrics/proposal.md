## Why

`codex_lb_accounts_total` is declared but never populated, so operators cannot alert on an empty account pool. Stored active status also differs from routing eligibility when an account requires reauthentication and its token has expired. This resolves #2426.

## What Changes

- Populate every account-status gauge, including zeroes, from the existing routing-account cache refresh.
- Add `codex_lb_accounts_available` using the shared status and reauthentication-token expiry rules. Exclude request-specific routing constraints.
- Refresh that snapshot before each metrics scrape so quiet pools, deletion, and token expiry are reflected without proxy traffic.
- Aggregate replicated whole-pool gauges using the most recently published live-worker observation, without summing workers or adding PID labels.
- Document metric scope, scrape failures, and example alerts.

## Impact

- Affected specification: `proxy-runtime-observability`.
- Affected code: routing-account cache, Prometheus gauges, metrics ASGI wiring.
- No routing policy, schema migration, setting, dashboard change, or runtime dependency is added.
