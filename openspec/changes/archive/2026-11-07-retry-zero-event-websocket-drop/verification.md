# Verification

The implementation uses the existing reconnect and replay admission path. Product-path tests cover both HTTP Responses routes with fresh replacement sockets and same-account preference; clean close 1000, transient close 1011, transport messages and typed receive errors; second-drop and response-progress refusal; and socket-local previous-response continuity. Unit coverage also exercises buffered output, durable acknowledgement, draining and cancellation during reconnect.

Final local results on Python 3.13 with frozen dependencies:

- `UV_PYTHON=3.13 make lint typecheck`: passed, including architecture, cancellation, timing, settings and migration topology checks.
- `UV_PYTHON=3.13 make test-unit PYTEST_ARGS='-q --tb=short --timeout=60 --timeout-method=thread -k "http_bridge or replay_safety or websocket_terminal_cancellation"'`: 1685 passed, 8904 deselected, 1 warning in 61.63s.
- `UV_PYTHON=3.13 make test-integration-bridge`: 370 passed, 3 warnings in 415.84s.
- Strict scoped OpenSpec validation passed; strict canonical validation passed all 67 specs.

Tests use synthetic accounts, upstream sockets and disposable databases. No production deployment or provider traffic is part of this verification. Supply-chain jobs were not changed or used as local acceptance criteria.
