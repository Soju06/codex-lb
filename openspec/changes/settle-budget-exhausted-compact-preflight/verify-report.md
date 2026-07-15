# Verification Report: settle-budget-exhausted-compact-preflight

## Summary

| Dimension | Status |
| --- | --- |
| Completeness | 10/10 tasks; 1/1 requirement implemented |
| Correctness | 3/3 scenarios covered |
| Coherence | Design and existing compact settlement patterns followed |

## Completeness

- All four budget-exhaustion terminals that bypass existing settlement handlers now release the API-key reservation before raising.
- Inner `_call_compact` budget terminals remain unchanged and are guarded by an exactly-once regression.
- Route-level tests cover all four fixed branches.
- A database-backed service test proves the reservation becomes `released`, quota returns to baseline, and an immediate next reservation succeeds.

## Correctness

- The fixed branches preserve the existing `502 upstream_request_timeout` error.
- Existing account-lease releases remain before the terminal error.
- Settlement uses `response=None`, selecting reservation release rather than usage finalization.
- The post-401 case does not perform forced refresh after the budget has expired.

## Coherence

- The implementation uses the same explicit settle-before-raise pattern as neighboring compact terminal branches.
- The delta is attached to the `api-keys` capability, which owns the reservation lifecycle contract.
- No schema, migration, dependency, or public API changes were introduced.

## Validation

- Related pytest suite: 30 passed.
- Focused post-type-fix regression recheck: 6 passed.
- Ruff check: passed.
- Ruff format check: passed for 752 files.
- Ty type check: passed.
- Proxy architecture check: passed.
- Strict change validation: passed.
- Main OpenSpec validation: 43 passed, 0 failed.

## Issues

No critical issues, warnings, or suggestions within this change's scope. Ready for archive after review.
