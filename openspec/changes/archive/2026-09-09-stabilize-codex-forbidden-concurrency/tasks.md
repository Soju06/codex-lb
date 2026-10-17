# Tasks

- [x] Add the narrow WebSocket edge-challenge classifier
      (`_is_upstream_edge_challenge`) with fail-closed evidence rules.
- [x] Stamp classified direct-connect edge challenges with the existing
      websocket transport-failure provenance so they ride the established
      surface/marker/bridge-replay recovery without account-health writes.
- [x] Extend the automatic-transport raw streaming fallback to retry over
      HTTP on a classified edge challenge, and preserve the classification
      for routed handshakes through `CodexTransportError`.
- [x] Preserve forced-WebSocket, permission, firewall, hard-pin, and
      reservation-settlement behavior.
- [x] Add unit regressions for challenge vs ordinary 403 classification,
      the failover decision, and the automatic HTTP fallback paths.
- [x] Run OpenSpec validation, focused pytest, ruff, and ty checks.
