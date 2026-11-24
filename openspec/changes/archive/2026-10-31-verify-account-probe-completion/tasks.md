## 1. Contract

- [x] 1.1 Define wakeup compatibility and bounded account-specific completion semantics.
- [x] 1.2 Add optional request mode and response completion fields.

## 2. Implementation

- [x] 2.1 Reuse account-aware route/transport and SSE framing with fixed request content and bounded execution.
- [x] 2.2 Preserve usage snapshots and prevent failed/incomplete tests from successful probe settlement.
- [x] 2.3 Preserve sanitized upstream error diagnostics and omit model-specific reasoning/text overrides.
- [x] 2.4 Use the normal HTTP stream client with optional bounded reads and actual HTTP status observation.

## 3. Verification

- [x] 3.1 Cover the actual probe API, HTTP-200 terminal failures, fragmented completion, early close, timeout, route ownership and cleanup.
- [x] 3.2 Run focused pytest, Ruff and strict OpenSpec validation; record evidence in verification.md.
- [x] 3.3 Verify terminal auth/quota distinction, credential redaction, and bounded structured HTTP errors.
- [x] 3.4 Verify normal/native transport, fingerprint and installation identity parity and unsupported field omission.
