## 1. Shutdown and WebSocket lifecycle

- [x] 1.1 Add a committed one-way deadline, a server/lifespan generation, a pre-connection Uvicorn barrier on every shipped or documented launch path, launcher API floor and failure semantics, and signal-neutral embedded metrics.
- [x] 1.2 Make WebSocket admission increment-first, count only Responses scopes, reject late turns, close idle sockets, and preserve terminal task ownership through cancellation.
- [x] 1.3 Make Helm preStop in-flight-driven on the shared deadline, add timing guards, and correct operator documentation.
- [x] 1.4 Document the Helm upgrade failure, computed termination-grace minimum and default, and operator remediation in OpenSpec and user-facing deployment guidance.

## 2. Regression coverage

- [x] 2.1 Add focused unit coverage for admission races, signal-reentrant deadline reuse, embedded lifecycle restart, clean-close/send-failure ownership, cross-account create-lease transfer, atomic pending-batch ownership, connection-lease partial failure, idle close, terminal delivery, real logging/settlement, cancel-once/close/bounded-await cleanup, and preStop behavior.
- [x] 2.2 Add and run real POSIX SIGTERM/SIGINT/Uvicorn/WebSocket process coverage for terminal-before-close, late admission, and bounded timeout.
- [x] 2.3 Render the Helm lifecycle hook and both timing-guard failures without mutating or downloading dependencies.

## 3. Verification

- [x] 3.1 Run focused tests plus Ruff and type checks for every changed module.
- [x] 3.2 Run strict OpenSpec validation, review the final diff/status, and complete legacy-strict ultrareview.
