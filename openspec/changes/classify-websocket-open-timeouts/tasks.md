- [x] 1.1 Add upstream WebSocket open-timeout failure-phase metadata.
- [x] 1.2 Persist a distinct `upstream_websocket_open_timeout` request-log
  error code for opening-handshake timeouts while preserving the client-facing
  `upstream_unavailable` error.
- [x] 1.3 Enrich service-tier mismatch trace logs with safe response, model,
  transport, and status context.
- [x] 1.4 Add live snapshot runtime-correlation request-log groups and recent
  request fields.
- [x] 1.5 Add focused unit coverage for timeout classification and diagnostic
  trace fields.
- [x] 1.6 Run focused unit tests, lint, and OpenSpec validation.
- [x] 1.7 Treat pre-visible upstream WebSocket open timeouts as bounded
  same-transport failover candidates while preserving the final client/log
  timeout contract.
