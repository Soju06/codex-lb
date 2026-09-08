## 1. Reconcile With Current Main

- [x] 1.1 Rebuild the branch on current `main`, preserve a backup ref, and confirm #1969/#1992 provide the canonical shared-future wait and previously overlapping call-site conversions.
- [x] 1.2 Remove redundant cancellation primitive, API-key, Compact, database teardown, streaming, and helper-body hunks; confirm contributor attribution already includes `mustafa0x`.

## 2. Preserve HTTP Bridge Cleanup Ownership

- [x] 2.1 Own terminal append and delivery barrier callbacks through canonical cancellation-deferring waits, preserving exactly-once invocation, terminal delivery ordering, and original cancellation.
- [x] 2.2 Own detached-session registry finalization after resource close so cancellation during its lock wait cannot strand bridge capacity.
- [x] 2.3 Add deterministic product-path regressions for grouped terminal cancellation and detached ownership finalization.

## 3. Prevent Structural Regression

- [x] 3.1 Add an AST repository check that rejects cancellation-catching retry loops around `asyncio.shield()`, including aliased, assigned, bare-except, `BaseException`, and AnyIO-shielded variants.
- [x] 3.2 Wire the check into the architecture gate, specify its structural contract under `proxy-architecture`, and add conditional-control-flow, module-alias, positive, negative, CLI-output, and repository-wide tests.

## 4. Validation

- [x] 4.1 Run focused HTTP bridge cancellation and checker tests.
- [x] 4.2 Run formatting, lint, type, proxy architecture, and cancellation-safety checks.
- [x] 4.3 Run strict change-local and repository OpenSpec validation, inspect the rebase range-diff, and record final evidence.
