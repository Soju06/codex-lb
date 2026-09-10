## 1. Implementation

- [x] 1.1 Share the client quota-header visibility policy across HTTP and WebSocket paths.
- [x] 1.2 Project native Codex rate-limit events from pooled headers and suppress unrepresented quotas.
- [x] 1.3 Apply projection after upstream ingestion on SSE, HTTP bridge, source SSE, and direct WebSocket egress.

## 2. Verification

- [x] 2.1 Cover divergent account/pool usage, hidden quotas, unknown/model-specific quotas, and cache failures.
- [x] 2.2 Verify response lifecycle, account usage ingestion, and source/bridge behavior remain intact.
- [x] 2.3 Run focused regression suites, lint, type and architecture checks, and strict OpenSpec validation.
- [x] 2.4 Sync the specs and archive the verified change.
