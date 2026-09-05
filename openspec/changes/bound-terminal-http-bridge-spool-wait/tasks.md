## 1. Contract

- [x] 1.1 Specify bounded terminal transcript persistence and fail-open delivery.

## 2. Implementation

- [x] 2.1 Bound terminal drain and append in the event batcher.
- [x] 2.2 Preserve incomplete-spool settlement and cancellation cleanup on timeout.
- [x] 2.3 Finalize only terminal append attempts observed to complete within the bound.
- [x] 2.4 Own terminal append/finalize tasks still pending past the bound on batcher close.

## 3. Verification

- [x] 3.1 Add batcher regression coverage for a stalled SQLite append.
- [x] 3.2 Add proxy-path coverage proving terminal delivery precedes fallback settlement.
- [x] 3.3 Run focused tests and strict OpenSpec validation.
- [x] 3.4 Cover concurrent context discard and late successful append completion.
- [x] 3.5 Cover batcher close draining a shielded terminal append/finalize past the bound.
