## Tasks

- [x] Add an OpenSpec delta for hard-affinity HTTP bridge retry behavior.
- [x] Add a regression test proving `session_header` bridge reconnect does not exclude the current account after upstream close `1011`.
- [x] Update HTTP bridge reconnect account selection to preserve hard-affinity account ownership.
- [x] Add HTTP bridge upstream WebSocket header filtering for create and reconnect paths.
- [x] Normalize responses WebSocket beta headers so HTTP Responses beta tokens are not forwarded upstream.
- [x] Run targeted tests and `openspec validate --specs`. HTTP bridge + websocket client unit tests, ruff, local HTTP/WebSocket smokes, repo-wide OpenSpec validation, and strict change validation passed.
