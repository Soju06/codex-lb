## Why

Nothing bounds an upstream streaming request between the TCP handshake and the first
response byte.

The shared session is built with `ClientTimeout(total=None)`, streaming requests pass
`sock_read=None`, and `upstream_connect_timeout_seconds` covers only the handshake. Once
a connection is established, a peer that never sends response headers is bounded solely
by `http_responses_stream_request_budget_seconds`, which defaults to 7200s. The stream
idle timeout does not apply, because it guards `_iter_sse_events`, which only runs after
headers exist.

That gap is expensive because the waiting request holds its session's
`response_create_gate` (an `asyncio.Semaphore(1)`). Observed in a single-tenant
deployment on 2026-08-05: one bridged request submitted at 16:29:18Z produced no
upstream event and no request-log row, while five later turns on the same session
retried gate acquisition every 10 seconds for 26 minutes and the Codex client gave up
with `stream disconnected before completion: idle timeout waiting for SSE`. Unrelated
traffic on the same account completed normally throughout.

Two conditions make the silent case likely and hard to see:

1. **A dead pooled socket is indistinguishable from a slow model.** Connectors retain
   idle connections for 90 seconds and enable no TCP keepalive probes, so a connection
   dropped by an intermediary (NAT rebind, tunnel reconnect, route change) is only
   discovered when the application layer eventually gives up.
2. **A dropped event leaves no trace.** A bridge session multiplexes one upstream
   connection across its pending requests; an event that matches none of them is
   discarded silently, so "upstream went quiet" and "the event arrived and was not
   routed" look identical in operations.

## What Changes

- The configured stream idle timeout MUST also bound the phase before response headers
  arrive, so "connected but silent" is treated the same as "streaming then silent". No
  new setting: `sock_read` carries the timeout that already exists.
- A socket read timeout MUST be reported as `stream_idle_timeout` rather than as an
  unavailable upstream, so the existing idle retry and failover paths apply.
- Upstream TCP connectors MUST enable OS-level keepalive probes, so a connection killed
  by an intermediary surfaces as a transport error instead of an indefinite wait. The
  existing pooled-reuse guarantees (`keepalive_timeout >= 90`, `ttl_dns_cache >= 300`)
  are unchanged.
- Upstream events that match no pending request MUST be logged with a stable
  low-cardinality reason while work is still waiting on the session.

## Impact

- Affected specs: `outbound-http-clients`, `proxy-runtime-observability`
- Affected code: `app/core/clients/http.py`, `app/core/clients/proxy.py`,
  `app/modules/proxy/_service/http_bridge/upstream_events.py`
- No new settings, no migration, no dashboard surface. Behavior changes only in failure
  paths that previously had no bound.
- A deployment that relies on upstream taking longer than `stream_idle_timeout_seconds`
  to send response headers would now see those attempts fail fast. That window is
  operator-configurable and defaults to two hours, so the practical blast radius is
  limited to genuinely dead connections.
- The stuck-gate retirement side of this failure is already handled on `main` by
  `_http_bridge_pending_state_is_stale`, which uses `last_upstream_activity_at` as the
  silence clock. This change is deliberately limited to the transport gap that lets a
  request go silent in the first place.
