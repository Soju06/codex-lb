## Implementation
- [x] 1. Reproduce and fix stale-snapshot suppression of committed rejection marks; retain same-account repair fencing.
- [x] 2. Reproduce and preserve unexpired cooldown eligibility after guarded credential repair.
- [x] 3. Reuse caller-owned encryptors in the touched credential paths.

## Verification
- [x] 4. Inspect the pinned-head CI failure and attempt its rerun; record permission or result separately from local tests.
- [x] 5. Independently review the exact original head and the final patch; resolve actionable findings.
- [x] 6. Run focused regressions, lint/type checks and strict specs; sync and archive verified artifacts.
