## 1. Regression coverage

- [x] 1.1 Add a scheduler-level test that loads a stale eligible account through a real async SQLAlchemy session and reproduces the detached candidate failure after session cleanup.
- [x] 1.2 Run the focused regression test against the current implementation and confirm it fails with `DetachedInstanceError` for the expected session-boundary reason.

## 2. Implementation

- [x] 2.1 Snapshot filtered and batch-limited candidate account IDs before the candidate-query repository session closes.
- [x] 2.2 Run the focused Auth Guardian tests and confirm the new regression and existing scheduling behaviors pass.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and verify the implementation against this change.
- [x] 3.2 Run the proportionate repository lint, type, and test gates for the changed Python and OpenSpec surfaces.
- [x] 3.3 Review the final diff for issue scope, session ownership, and simplicity-gate compliance.
