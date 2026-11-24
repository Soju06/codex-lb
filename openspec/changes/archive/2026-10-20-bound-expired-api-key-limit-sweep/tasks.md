## 1. Reproduce and implement

- [x] 1.1 Run the issue's SQLite/NullPool reproducer against unchanged code and confirm its ten-second timeout.
- [x] 1.2 Add SQLite and unit regressions and confirm they fail before the fix.
- [x] 1.3 Add primary-key pagination while preserving compare-and-set and batch commits; verify the reproducer and focused tests pass.

## 2. Validate and document

- [x] 2.1 Run the repository/scheduler unit tests, API-key integration suite, `make lint`, and `make typecheck`; record results.
- [x] 2.2 Sync the requirement and context, validate the change strictly and validate all main specs, and verify implementation/scenario coverage before archiving.
