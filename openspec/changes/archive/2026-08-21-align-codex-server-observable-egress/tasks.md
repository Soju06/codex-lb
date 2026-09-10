## 1. Regression

- [x] 1.1 Add model-discovery tests for Codex identity, wildcard accept, account header, and absence of aiohttp identity leakage.
- [x] 1.2 Add bridge-admission tests covering `always_http`, smart sticky/non-sticky behavior, `always_websocket`, explicit transport precedence, and per-key overrides.
- [x] 1.3 Add traffic-report fixtures that keep HTTP, SSE, WebSocket, TLS, and identity-mismatch dimensions distinct.

## 2. Implementation

- [x] 2.1 Introduce the shared control-request header builder and use it for direct and routed model discovery.
- [x] 2.2 Gate HTTP bridge activation with the shared effective transport-policy resolver.
- [x] 2.3 Align direct and routed upstream WebSocket extension offers with Codex.
- [x] 2.4 Add the internal native egress request/stream contract and a Codex-stack implementation slice without adding required setup.

## 3. Verification

- [x] 3.1 Run focused unit/integration tests, formatting, lint, and type checks for changed files.
- [x] 3.2 Run strict OpenSpec validation.
- [x] 3.3 Repeat controlled direct-vs-LB captures and record which server-observable gaps closed and which remain.
