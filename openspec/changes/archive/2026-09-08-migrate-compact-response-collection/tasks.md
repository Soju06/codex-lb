## 1. Contract and collector
- [x] 1.1 Add the synchronous Responses collector and shared Python/Rust fixtures.
- [x] 1.2 Extend negotiated SSE options and fragmented compact result contract.
## 2. Native cutover
- [x] 2.1 Compose framing and collection, stopping on terminal events before EOF.
- [x] 2.2 Consume native results in Python while retaining public translation and pre-dispatch fallback.
## 3. Verification
- [x] 3.1 Verify output parity, large results, terminal errors, cancellation, and no replay through direct/routed paths.
- [x] 3.2 Run Rust checks, Python regressions, lint/types, architecture, and strict specs.
- [x] 3.3 Record performance baseline and verification, sync specs, and archive.
