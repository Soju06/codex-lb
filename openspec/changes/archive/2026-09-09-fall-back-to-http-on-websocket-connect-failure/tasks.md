# Tasks: fall-back-to-http-on-websocket-connect-failure

## 1. Implementation

- [x] 1.1 Stamp host-scoped transport provenance at the direct upstream
      websocket open (connect timeout, invalid handshake, 5xx upgrade
      rejection, non-host-wide connect network error) and withhold it from
      credential-scoped rejections, TLS verification failures, host-wide
      network loss and every routed open; surface failures carrying that
      provenance without recording account failure health or rotating
      accounts, and keep everything else on the penalized failover path
- [x] 1.2 Arm a bounded per-instance transport-failure marker on that surface
      path and when a websocket open exhausts the request budget after the
      direct upstream connector began, and clear it on the next successful
      direct upstream websocket connect
- [x] 1.3 Deny responses websocket handshakes with HTTP 426 while the marker
      is armed or `upstream_stream_transport` is pinned to `"http"`, except
      for capability-bearing handshakes, and reject a capability signal
      carried in `client_metadata` on every HTTP route that parses a
      Responses-shaped body (native, v1, both compact, internal bridge)
- [x] 1.4 Bypass the HTTP responses bridge and pin the raw path's upstream
      transport to `"http"` while the marker is armed or the upstream
      transport is pinned to `"http"`
- [x] 1.5 Fall back from a bridge session-creation failure carrying
      pre-submit provenance to raw HTTP streaming, never replaying
      post-submit failures, never replaying a turn whose continuity anchor
      only the bridge's prepared payload carries, and skipping the fallback
      while an API-key usage reservation is unsettled
- [x] 1.6 Arm the transport-failure marker from the bridge fallback, whose
      pre-dispatch failover never reaches the websocket failover decision
- [x] 1.7 Tag replay-safe cooldown suppressions at the bridge retry
      circuit's pre-dispatch submission gate with a dedicated provenance
      marker plus the shared pre-submit provenance, and admit them to the
      wrapper's raw-HTTP fallback on that marker rather than the shared
      `upstream_request_timeout` code, which pre-submit budget exhaustion
      also emits; ambiguous continuations, including a payload-scoped
      `conversation`, keep the bounded 503

## 2. Regression coverage

- [x] 2.1 Failover decision: transport-provenance failures surface without
      penalty and arm the marker; account-scoped, refresh and sub-5xx
      failures keep the penalized failover path
- [x] 2.2 Connect-site provenance through the real client conversion: direct
      5xx handshake, direct connect timeout, direct credential rejection,
      direct and routed TLS verification failure, routed 5xx handshake
- [x] 2.3 Handshake admission: 426 denial while armed or pinned, normal accept
      otherwise, marker TTL expiry and clear; a capability handshake connects
      while armed, and a metadata-only capability signal is rejected on
      every HTTP responses-shaped route while benign metadata keeps routing
- [x] 2.4 Budget exhaustion: a stalled direct connector arms the marker; a
      budget that expires in local admission or in a routed connector does
      not; a direct open success clears the marker and a routed one does not
- [x] 2.5 Bridge: pinned-http bypass, transient pre-stream fallback, marker
      arming, provenance-classified fallback for a direct 5xx connect code,
      the real bridge recording prepared-anchor provenance, and the negative
      cases (prepared anchor, routed connect, partial stream, API-key
      reservation, refresh provenance, non-transient failure) at
      `_stream_http_bridge_or_retry`
- [x] 2.6 Cooldown gate: replay-safety predicate covers every
      unambiguous-boundary marker; tagged suppressions fall back, untagged
      suppressions and same-code budget exhaustions propagate at
      `_stream_http_bridge_or_retry`

## 3. Verification

- [x] 3.1 Run focused unit suites and strict OpenSpec validation
- [x] 3.2 Validate live against a real websocket-only upstream outage
      (2026-08-22): first turn surfaces the 502 and arms the marker, the next
      handshake is denied with 426, and Codex turns complete over HTTP
