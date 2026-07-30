# Release idle HTTP bridge sessions' account stream leases between turns

## Why

An HTTP bridge session acquires its per-account stream lease at creation and releases it only when the session closes — after the idle TTL (up to 900 seconds for Codex sessions). The per-account stream cap therefore counts warm-but-idle WebSockets as active streams: a client that finished its turn keeps a cap slot for up to 15 minutes, and a burst of short-lived sessions can hold every slot of an account while generating nothing. On a live 2-replica deployment, ~120 leases from finished (client-abandoned) sessions kept every account at its cap for the full idle TTL, rejecting new work against capacity that no active generation was using.

## What Changes

- When a session's last in-flight turn detaches (no queued requests, no admission waiters, no pending requests), the session releases its account stream lease while staying alive for reuse; the idle upstream WebSocket no longer occupies a cap slot.
- The next turn admitted to an idle session reacquires a lease under normal cap admission before it is counted into the session queue. Denial raises the standard HTTP 429 `account_stream_cap` envelope, so the existing recoverable capacity wait and client retry semantics apply unchanged.
- Session close keeps its existing release path; a session that already released idles simply has nothing to release at close.
- The stream recovery reserve is not consulted at reacquisition, consistent with the existing requirement that the reserve is a selection-time reserve.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Per-account stream caps bound concurrent in-flight turns rather than open bridge sessions; idle sessions must not hold stream leases, and reacquisition is cap-admitted with the standard local-cap failure.

## Impact

- `app/modules/proxy/_service/http_bridge/request_submit.py`: idle release and turn-admission reacquisition.
- `app/modules/proxy/_service/http_bridge/streaming.py`: release check when a turn's stream finalizes.
- Cap accounting (`codex_lb_account_inflight_leases`) now reflects active turns, so operators see true generation concurrency; effective per-account throughput rises because idle sessions no longer starve admission.
- No setting, migration, dashboard, or API schema change.
