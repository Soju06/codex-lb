## 1. Specification

- [x] 1.1 Define latest-suite selection and stale-check handling for Codex label synchronization.

## 2. Implementation

- [x] 2.1 Identify the GitHub Actions run containing the newest `CI Required` check.
- [x] 2.2 Exclude non-required checks from superseded Actions runs without masking current-suite failures.

## 3. Verification

- [x] 3.1 Add positive and negative unit regressions for duplicate same-head CI runs.
- [x] 3.2 Run focused tests, lint, type checks, and strict OpenSpec validation.

## 4. Issue #1182 follow-up

- [x] 4.1 Order workflow runs by the current attempt's `run_started_at`, with
  workflow creation time as a compatibility fallback.
- [x] 4.2 Cover pending and failed manual reruns of an older workflow `run_id`.
- [x] 4.3 Run the 25-test synchronizer suite, Ruff format/check, `ty`, strict
  change validation, all-spec strict validation, and `git diff --check`.
