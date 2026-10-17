## 1. Scope and Implementation

- [x] 1.1 Restore existing OAuth behavior and all pre-existing tests; remove Docker changes from this PR.
- [x] 1.2 Add one store-owned expiry task with deadline recalculation on start and pending-flow hydration.
- [x] 1.3 Drain the task on reset and preserve existing idle-listener shutdown.
- [x] 1.4 Add the PR author to the contributor registry.

## 2. Verification

- [x] 2.1 Prove abandoned and overlapping listener regressions fail on the original main and pass with the fix.
- [x] 2.2 Verify hydrated earlier deadlines and reset cleanup.
- [x] 2.3 Run the complete OAuth integration file, contributor tests, lint, type checks, and strict change validation.
