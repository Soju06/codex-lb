## Why

codex-lb learns account quota state exclusively by polling `/backend-api/wham/usage` (default every 60s, one account per scheduler tick). Upstream codex clients have long consumed fresher signals that ride on the traffic itself: `x-codex-{primary,secondary}-*` response headers and `codex.rate_limits` stream events carry the same windows on every proxied turn. Because codex-lb discards them, selection state lags real usage by up to a refresh interval, in-flight pressure is approximated with a synthetic penalty, and bursty traffic can exhaust a window well before the poller notices. With the 5h window temporarily removed upstream, per-turn signals are also the fastest way to observe when short windows disappear or return.

## What Changes

- Proxied upstream responses become a passive usage source: rate-limit response headers and `codex.rate_limits` stream events observed on the HTTP/SSE path and the upstream WebSocket bridge are parsed into snapshots and written through the existing usage-history semantics.
- A per-account ingest throttle (change fingerprint + minimum write interval) bounds write volume; ingestion never blocks or fails the serving path, and publishes through a startup-registered hub so the core client layer stays decoupled from module-layer persistence.
- The background poller remains authoritative for accounts without live traffic and for payload-only fields; live rows naturally satisfy its freshness gate, so polling pressure drops on busy accounts without configuration changes.
- Ingestion is enabled by default with an env kill switch.

## Capabilities

### New Capabilities

- `live-usage-ingestion`: passive per-turn usage snapshots from proxied traffic.

### Modified Capabilities

None (usage-refresh-policy semantics are unchanged; live rows flow through the same storage contract).

## Impact

- Code: `app/core/usage/live_snapshots.py` (new), `app/core/usage/live_hub.py` (new), `app/modules/usage/live_ingest.py` (new), `app/core/clients/proxy.py`, `app/modules/proxy/_service/http_bridge/upstream_events.py`, `app/main.py`, `app/core/config/settings.py`
- Tests: parser/ingestor unit suites, SSE and bridge integration coverage
- Specs: `openspec/specs/live-usage-ingestion/spec.md` (new)
