# Tasks: fall-back-to-http-on-websocket-connect-failure

## 1. Implementation

- [x] 1.1 Surface server-level transient websocket connect failures
      (`upstream_unavailable` / `upstream_websocket_handshake_failed`, 5xx)
      carrying `failure_phase = "connect"` provenance without recording
      account failure health or rotating accounts; failures without connect
      provenance (OAuth refresh transport errors) keep the penalized
      failover path
- [x] 1.2 Arm a bounded per-instance transport-failure marker on that surface
      path and when a websocket open exhausts the request budget, and clear
      it on the next successful upstream websocket connect
- [x] 1.3 Deny responses websocket handshakes with HTTP 426 while the marker
      is armed or `upstream_stream_transport` is pinned to `"http"`
- [x] 1.4 Bypass the HTTP responses bridge and pin the raw path's upstream
      transport to `"http"` while the marker is armed or the upstream
      transport is pinned to `"http"`
- [x] 1.5 Fall back from a bridge session-creation failure carrying
      pre-submit provenance to raw HTTP streaming, never replaying
      post-submit failures and skipping the fallback while an API-key usage
      reservation is unsettled

## 2. Regression coverage

- [x] 2.1 Failover decision: transient 5xx surfaces without penalty and arms
      the marker; account-scoped and sub-5xx failures keep the penalized
      failover path
- [x] 2.2 Handshake admission: 426 denial while armed or pinned, normal accept
      otherwise, marker TTL expiry and clear
- [x] 2.3 Bridge: pinned-http bypass, transient pre-stream fallback, and the
      negative cases (partial stream, API-key reservation, non-transient
      failure) all covered at `_stream_http_bridge_or_retry`

## 3. Verification

- [x] 3.1 Run focused unit suites and strict OpenSpec validation
- [x] 3.2 Validate live against a real websocket-only upstream outage
      (2026-08-22): first turn surfaces the 502 and arms the marker, the next
      handshake is denied with 426, and Codex turns complete over HTTP
