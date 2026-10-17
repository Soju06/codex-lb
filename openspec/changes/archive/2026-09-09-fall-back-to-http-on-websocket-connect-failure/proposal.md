# Fall back to HTTP on websocket connect failure

## Why

When the Codex upstream websocket endpoint degrades while plain HTTPS to the
same backend stays healthy (observed in production on 2026-08-22: websocket
connects timing out at the connect deadline for ~15 minutes while the model
registry refresh and usage polls kept succeeding), codex-lb currently turns the
transport outage into an account-availability outage:

- every websocket connect timeout records a transient account error, so a
  retrying client drives all serving accounts into `error_count` backoff;
- hard session affinity then fails follow-up turns closed with
  `previous_response_owner_unavailable` on every transport, for up to five
  minutes past upstream recovery;
- the HTTP responses bridge cannot help because it establishes upstream
  websocket sessions itself, and its session creation fails on the same
  timeouts.

The existing recovery assumption is also wrong for current Codex clients. The
websocket model-source guard emits an in-band service-level 503 expecting the
client to retry over HTTPS, but codex-rs (checked against `rust-v0.149.0`,
`codex-rs/core/src/client.rs`) activates its session-scoped HTTP transport
fallback **only** when the websocket handshake is rejected with HTTP 426
(`StatusCode::UPGRADE_REQUIRED`); wrapped in-band error events map to
transport errors that retry on the websocket transport. During the observed
outage a Codex 0.149.0 client reconnected over websocket every ~9 seconds
indefinitely instead of falling back.

## What Changes

- A server-level transient websocket connect failure (5xx
  `upstream_unavailable` / `upstream_websocket_handshake_failed`) whose
  provenance is the websocket open itself (`failure_phase = "connect"`)
  surfaces to the client immediately instead of penalizing the selected
  account and rotating to the next account, so a transport-level failure no
  longer consumes account health or breaks hard-affinity selection for the
  client's HTTP retry. Failures that share the `upstream_unavailable`
  envelope without connect provenance — OAuth refresh transport errors in
  particular — keep the existing classify-penalize-failover path toward
  healthy accounts.
- The same failure — and a websocket open that consumes the request budget
  without completing — arms a bounded (60 s) per-instance transport-failure
  marker. While it is armed — or while `upstream_stream_transport` is pinned
  to `"http"` — the responses websocket routes deny new handshakes with HTTP
  426, the one signal that activates the Codex client's session-scoped HTTP
  transport fallback. The marker clears on the next successful upstream
  websocket connect, so service returns to the websocket transport
  automatically after the outage.
- The HTTP responses bridge honors a pinned `"http"` upstream transport by
  bypassing the bridge; while the transport-failure marker is armed, bridged
  and raw HTTP requests pin the upstream transport to `"http"` (a sticky
  follow-up moved to the HTTP route must not resolve back onto the
  unavailable websocket upstream); and bridge session creation that fails
  with a transient 5xx `upstream_unavailable` carrying pre-submit
  session-creation provenance falls back to raw HTTP streaming before any
  line reached the client (never replaying post-submit failures, and skipped
  while an API-key usage reservation is unsettled, preserving the
  reservation-settlement invariant).

No new settings; the behavior is zero-config and self-recovering.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: websocket-only upstream outages degrade to the HTTP
  transport instead of failing turns and poisoning account health, and the
  handshake-level 426 contract with Codex clients is documented as normative.

## Impact

- Code: websocket connect failover decision, responses websocket route
  admission, HTTP bridge transport gating and startup fallback.
- Tests: unit coverage for the failover decision, the 426 handshake denial,
  and the bridge bypass/fallback paths.
- API/schema: websocket handshakes can now be denied with HTTP 426 during
  outages or under a pinned HTTP transport; no database or configuration
  change.
