## 1. Retag Planning and Backup Tests

- [x] 1.1 Add a regression test proving JSONL target selection and provider counts share one metadata-only discovery pass.
- [x] 1.2 Add tests for hard-link backup success and safe copy fallback before atomic replacement.
- [x] 1.3 Add tests proving SQLite planning removes duplicate count queries and verification is target-only.

## 2. Retag Core Implementation

- [x] 2.1 Implement the immutable JSONL and SQLite retag plan.
- [x] 2.2 Implement hard-link-first JSONL backup with flushed copy fallback.
- [x] 2.3 Implement single-pass atomic JSONL conversion and targeted post-verification.
- [x] 2.4 Emit structured phase and byte/item progress events while preserving the human-readable CLI summary.

## 3. Subprocess supervision contract

- [x] 3.1 Emit a backup identity before long mutation phases and preserve the final backup identity in the summary.
- [x] 3.2 Define progress-event semantics that allow an external supervisor to implement idle detection.
- [x] 3.3 Keep process termination, UI display, and rollback orchestration outside the CLI implementation contract.

## 4. Verification and Packaging

- [x] 4.1 Run focused and full codex-lb retag/CLI tests plus lint and strict OpenSpec validation.
- [x] 4.2 Run strict OpenSpec validation for the CLI contract.
- [x] 4.3 Benchmark an isolated large-session fixture and verify no live provider switch or user-session retag is performed.
- [x] 4.4 Produce an approval-safe CLI handoff without changing live provider configuration or containers.
