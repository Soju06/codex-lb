## 1. Preserve and integrate

- [x] 1.1 Preserve the dirty backport work and unrelated installer artifact, verify a recoverable snapshot and frozen beta.6 commit, then begin a no-commit merge.
- [x] 1.2 Resolve native adapter, WebSocket metadata, reset scheduler, tests, settings reference, and OpenSpec conflicts; verify no unresolved merge entries or markers remain.
- [x] 1.3 Restore backport-only sanitation, settlement-order fixes, and regression tests without reversing beta.6 changes; verify focused proxy tests.

## 2. Migration and compatibility

- [x] 2.1 Add the migration merge revision without changing the deployed group revision; verify fresh, fork-head, and upstream-head upgrades preserve data and converge to one head.
- [x] 2.2 Verify UTC+7 reset windows, usage-group dashboard behavior, native byte budgets, interpretation metadata, and failure diagnostics with regression tests.
- [x] 2.3 Synchronize canonical requirements and context with integration deltas; verify strict OpenSpec validation.

## 3. Integrated verification and handoff

- [x] 3.1 Install frozen dependencies, rebuild and test the native helper, and verify frontend typecheck/tests/build.
- [x] 3.2 Run broad relevant backend and static checks in an isolated test environment; record exact results and any limitations in verification.md.
- [x] 3.3 Verify implementation against change artifacts, archive only after verification, and hand off the pending local merge without commit, push, or deployment.
