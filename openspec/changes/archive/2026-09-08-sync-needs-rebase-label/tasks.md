## 1. Label synchronization

- [x] 1.1 Classify confirmed conflict, known non-conflict, and unknown merge states.
- [x] 1.2 Add or remove `needs rebase` through the existing idempotent label write path.
- [x] 1.3 Ensure the label exists and expose its decision in dry-run output.

## 2. Validation

- [x] 2.1 Cover merge-state decisions and exact GitHub API writes with unit tests.
- [x] 2.2 Verify current live conflict, stale-label, and unknown-state examples in read-only dry-run mode.
- [x] 2.3 Validate the focused test suite, Ruff, and OpenSpec strictly.
