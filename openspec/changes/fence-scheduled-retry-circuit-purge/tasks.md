## 1. Regression and implementation

- [x] 1.1 Demonstrate same-timestamp claim/failure races and changed observations through the production scheduled cleanup repository path; confirm regressions fail on pinned main.
- [x] 1.2 Match selected existing-column fences and stop after a deletion miss; verify the regression and existing retention/scheduler tests pass.

## 2. Independent integration

- [x] 2.1 Verify the delta has no unmerged receipt schema/helper dependency and demonstrate compatibility with the receipt candidate in both application orders without editing its original branch.
- [x] 2.2 Run lint, type and strict OpenSpec validation; sync the new requirement and record the tested candidate and boundaries.

- [ ] 2.3 Verify the new PostgreSQL race tests on current-head hosted CI before calling the draft ready.

- [x] 2.4 Fence detail-only settlement transitions, including unresolved anchor registration, and verify null detail deletion.
