## 1. Implementation

- [x] 1.1 Preserve the raw HTTP/WebSocket peer before one Uvicorn-compatible proxy projection and disable server projection on owned launch paths
- [x] 1.2 Use the preserved peer for locality and `proxy_unauthenticated_client_cidrs`, failing closed when capture is absent
- [x] 1.3 Add locality-only identity-family consensus while preserving generic resolver precedence and per-family behavior

## 2. Regression Evidence

- [x] 2.1 Cover agreeing, disagreeing, malformed, empty, repeated, singleton, chain, untrusted-peer, and generic-precedence cases
- [x] 2.2 Prove the owned HTTP/WebSocket boundary rejects projected allowlist bypasses and conflicting identities while accepting agreement
- [x] 2.3 Run focused tests, Ruff, ty, strict OpenSpec validation, full fast CI, bridge tests, diff checks, and a clean Codex review
