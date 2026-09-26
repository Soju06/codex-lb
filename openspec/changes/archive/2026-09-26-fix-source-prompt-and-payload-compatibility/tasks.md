## 1. Regression coverage

- [x] 1.1 Add maintained public-route regressions for all four reproduced findings and record fail-before evidence.
- [x] 1.2 Add positive controls, override and malformed-variable cases, streaming SDK coverage, and shared-replica prompt continuity/credential replacement tests; verify their targeted results.

## 2. Fixes

- [x] 2.1 Include prompt IDs in durable scoped source references; verify matching, unknown, conflicting, overridden, and replaced-credential cases.
- [x] 2.2 Reject file-bearing effective prompt variables before admission/reservation; verify client and override payloads plus reference-free controls.
- [x] 2.3 Use source forwarding serialization for original references in dispatch and lookup misses; verify external compaction acceptance and known-owner denial.
- [x] 2.4 Classify valid top_logprobs as direct-source neutral; verify boundary/malformed values and HTTP/SDK streaming without broadening subscription replay.

## 3. Verification

- [x] 3.1 Run focused regression, ownership/settlement, shared PostgreSQL replica, lint, format, and type checks; record actual results and limitations.
- [x] 3.2 Complete independent review of the fix and address actionable related findings; retain review evidence.
- [x] 3.3 Sync requirements/context, validate OpenSpec strictly, verify completed tasks against evidence, and archive the verified change.
