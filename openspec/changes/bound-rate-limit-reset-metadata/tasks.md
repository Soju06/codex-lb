## 1. Reset metadata validation

- [x] 1.1 Add a deterministic, finite 366-day plausibility validator for absolute and relative rate-limit reset metadata.
- [x] 1.2 Make rate-limit and quota handlers prefer valid explicit metadata and otherwise retain the existing Retry-After/backoff fallback.

## 2. Persisted-state recovery

- [x] 2.1 Treat implausible persisted rate-limit deadlines as missing metadata during account-selection reconstruction while preserving the minimum block floor.
- [x] 2.2 Let background usage refresh recover implausibly blocked rows from fresh available-quota evidence through the existing compare-and-set write.

## 3. Regression coverage

- [x] 3.1 Add unit coverage for plausible, wrong-unit, non-finite, expired, and valid-relative-fallback metadata.
- [x] 3.2 Add selection and usage-refresh regressions proving a poisoned persisted row self-heals without weakening valid cooldowns.

## 4. Validation

- [x] 4.1 Run focused balancer and usage-refresh tests plus lint/format checks for touched Python files.
- [x] 4.2 Run strict OpenSpec validation and verify implementation/spec/task coherence.
