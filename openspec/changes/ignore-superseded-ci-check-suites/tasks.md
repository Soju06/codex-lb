## 1. Specification

- [x] 1.1 Define latest-suite selection and stale-check handling for Codex label synchronization.

## 2. Implementation

- [x] 2.1 Identify the GitHub Actions run containing the newest `CI Required` check.
- [x] 2.2 Exclude non-required checks from superseded Actions runs without masking current-suite failures.

## 3. Verification

- [x] 3.1 Add positive and negative unit regressions for duplicate same-head CI runs.
- [x] 3.2 Run focused tests, lint, type checks, and strict OpenSpec validation.
