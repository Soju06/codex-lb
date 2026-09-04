## 1. Multi-file import behavior

- [x] 1.1 Add focused import-dialog regressions for selecting multiple files, sequential submission, successful close/reset, and partial-failure retry state; verify the focused Vitest file fails before the implementation.
- [x] 1.2 Update the account import dialog to retain all selected files, process them sequentially, and retain only failed/unattempted files after an error; verify the focused Vitest file passes.
- [x] 1.3 Update all dashboard locale bundles for multi-file instructions and pending-file labeling; verify i18n locale parity tests pass.

## 2. Verification and specification sync

- [x] 2.1 Run focused account frontend tests plus frontend typecheck and lint, and resolve any regressions.
- [x] 2.2 Run strict OpenSpec validation, sync the verified delta into the main frontend architecture spec, and confirm the archived change has no incomplete tasks.
