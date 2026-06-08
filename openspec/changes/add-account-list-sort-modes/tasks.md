## 1. Spec Delta

- [x] 1.1 Add an Accounts page sort-mode requirement.
- [x] 1.2 Cover reset-soonest, reset-latest, and name sort semantics.
- [x] 1.3 Specify that unknown or elapsed reset timestamps sort last in reset-time modes.

## 2. Verification

- [x] 2.1 Validate the OpenSpec change with `uv run openspec validate add-account-list-sort-modes --strict`.
- [x] 2.2 Run focused account-list tests.
- [x] 2.3 Run frontend typecheck.
