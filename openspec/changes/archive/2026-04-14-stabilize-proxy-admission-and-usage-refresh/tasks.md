## 1. Specs

- [x] 1.1 Add `proxy-admission-control` requirements for split downstream lanes, compact protection, and explicit overload responses.
- [x] 1.2 Add `usage-refresh-policy` requirements for cooldown and `401` deactivation-signal handling.
- [x] 1.3 Extend `proxy-runtime-observability` with local admission rejection logging requirements.
- [x] 1.4 Validate OpenSpec changes.

## 2. Tests

- [x] 2.1 Add bulkhead middleware regression coverage for split lanes and websocket HTTP denial responses.
- [x] 2.2 Add proxy service unit coverage for response-create admission and local-overload surfacing.
- [x] 2.3 Add auth manager regression coverage for refresh singleflight and short failure caching.
- [x] 2.4 Add usage updater regression coverage for cooldown and `401` deactivation-signal handling.
- [x] 2.5 Add settings coverage for the new admission-control defaults and env overrides.

## 3. Implementation

- [x] 3.1 Add configurable downstream admission lanes for proxy HTTP, proxy websocket, compact HTTP, and dashboard traffic.
- [x] 3.2 Return explicit local-overload envelopes and `Retry-After` on HTTP and websocket-handshake admission rejections.
- [x] 3.3 Add second-stage admission controls around token refresh, upstream websocket connect, and first-turn response creation.
- [x] 3.4 Singleflight forced refreshes and short-circuit rapid repeat failures.
- [x] 3.5 Add usage-refresh cooldown for repeated `401`/`403` failures and deactivate on deactivation-signaling `401` messages.
- [x] 3.6 Emit structured logs for local admission rejections.
