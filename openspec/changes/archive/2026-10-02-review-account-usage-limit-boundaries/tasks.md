## 1. Authorization boundaries

- [x] 1.1 Reproduce and fix HTTP bridge dispatch/prewarm waits; verify route-level rejection, no upstream send, and released admission accounting.
- [x] 1.2 Reproduce and fence selection invalidation during sticky persistence; verify public selection returns no account or lease.
- [x] 1.3 Reproduce and invalidate empty successful polls for capped accounts; verify dashboard state and routing denial with ordinary and monthly history, plus disabled-policy compatibility.
- [x] 1.4 Preserve the canonical HTTP Responses policy-denial status and envelope; verify streaming, nonstreaming, backend SSE, and upstream-exhaustion compatibility.
- [x] 1.5 Merge latest upstream locally, resolve conflicts against its current contracts, and join migration heads with upgrade and policy-data coverage.

## 2. Architecture and verification

- [x] 2.1 Review remaining production changes, simplify demonstrated redundant work, and document findings and decisions with code references.
- [x] 2.2 Run appropriate backend/frontend, migration, lint, typing, and strict OpenSpec checks; record exact results and limitations.
- [x] 2.3 Verify requirements against implementation and tests, sync stable context, archive the verified change, and deliver focused local commits.
