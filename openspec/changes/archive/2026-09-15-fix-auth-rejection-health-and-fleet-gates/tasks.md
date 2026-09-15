## 1. Review Corrections

- [x] 1.1 Add a failing real-database refresh-only rotation/rejection regression, fix access-only staleness detection, and verify token-CAS safeguards.
- [x] 1.2 Reproduce health writes after rejection through the real balancer, preserve rejection atomically, and verify cooldown expiry, repair, and operator behavior.
- [x] 1.3 Fix fleet filtering and verify HTTP attempted counts and actual upstream work across credential states.

## 2. Verification

- [x] 2.1 Run focused suites and full lint/type checks; independently review the combined patch and address findings.
- [x] 2.2 Sync canonical specs/context and strictly validate the verified change before archival.
- [x] 2.3 Prepare the reviewed patch for authorized publication and record the pending cloud-check boundary in the verification artifact.
