## 1. Retire label synchronization

- [x] 1.1 Delete the Codex review label workflow, synchronization script, and dedicated unit tests.
- [x] 1.2 Discard the in-flight `label-sync-rate-limit-fallback` OpenSpec change.

## 2. Update repository contracts and guidance

- [x] 2.1 Replace the documented cloud Codex merge gate with the current-head CodeRabbit finding gate while retaining local Codex review as optional guidance.
- [x] 2.2 Remove stale label-sync references from CI and simplicity-budget workflow comments without changing check names or label-event triggers.
- [x] 2.3 Record removal of every label-sync requirement in the `github-automation` delta while preserving unrelated CI and simplicity-budget requirements.

## 3. Verification

- [x] 3.1 Validate the OpenSpec change and classify every remaining retired gate-term hit as historical, local-harness, or OpenSpec removal evidence.
- [x] 3.2 Run repository lint, type checks, and the unit test suite.
