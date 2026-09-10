## 1. Regression Matrix

- [x] 1.1 Add deterministic real HTTP middleware coverage for raw-versus-projected trust rows and missing capture.
- [x] 1.2 Add deterministic real WebSocket firewall helper coverage for raw-versus-projected trust rows and missing capture.
- [x] 1.3 Run the focused matrix before production edits and preserve the RED output.

## 2. Firewall Binding

- [x] 2.1 Supply the captured raw peer to HTTP middleware and helper firewall resolution.
- [x] 2.2 Supply the captured raw peer to protected WebSocket firewall resolution.
- [x] 2.3 Run the focused matrix GREEN while retaining trusted-chain, malformed, repeated, singleton, cache, and allow-all coverage.

## 3. Verification

- [x] 3.1 Run focused suites, Ruff, ty, strict OpenSpec validation, formatting, diff checks, and changed-file diagnostics.
- [x] 3.2 Execute an ASGI HTTP/WebSocket real-surface proof showing raw-peer enforcement with downstream client/scheme projection preserved.
- [x] 3.3 Remove temporary driver state and review the final diff against the security disposition and scope constraints.
