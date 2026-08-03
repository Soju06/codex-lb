## 1. Transport liveness

- [x] 1.1 Enable the existing finite heartbeat and ping timeout for routed and direct Responses WebSocket connections.
- [x] 1.2 Classify aiohttp heartbeat expiry and websockets keepalive expiry with the stable liveness-timeout code and a shared account-neutral predicate.

## 2. Relay safety

- [x] 2.1 Make direct Responses WebSocket liveness failures terminal, non-replayable, account-neutral, and fully settled.
- [x] 2.2 Apply the same no-replay, account-neutral, forced-retirement behavior to HTTP bridge upstream readers.

## 3. Regression coverage

- [x] 3.1 Cover direct and routed transport policy values and library-specific liveness classification.
- [x] 3.2 Cover direct WebSocket and HTTP bridge no-replay, account-health, settlement, and retirement invariants.

## 4. Verification

- [x] 4.1 Run focused tests, formatting, lint, type, architecture, and strict OpenSpec validation checks.
