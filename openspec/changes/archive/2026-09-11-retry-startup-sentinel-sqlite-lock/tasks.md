## 1. Shared retry
- [x] 1.1 Factor the fingerprint stamp's inline retry into a shared bounded helper with one module-level budget.
- [x] 1.2 Route the hard-sticky outage-grace startup seed through the same helper, rolling back between attempts.

## 2. Diagnosis
- [x] 2.1 Log `sqlite_errorname` and elapsed time on retry (DEBUG) and on give-up (WARNING), without logging written values.

## 3. Coverage
- [x] 3.1 Cover the retried transient seed, the exhausted budget's preserved outcome and named error, the missing-table degrade, and the unchanged fingerprint behaviour.
- [x] 3.2 Pass lint, type check, migration check, the touched suites, and strict OpenSpec validation.
