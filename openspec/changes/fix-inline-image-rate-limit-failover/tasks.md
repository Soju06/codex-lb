## 1. Contract

- [x] 1.1 Add the soft-affinity reallocation requirement and required-owner
  non-regression scenario to the Responses compatibility spec.

## 2. Implementation

- [x] 2.1 Mark soft affinity reallocatable after excluding an account from a
  pre-visible stream rate-limit or quota failover.
- [x] 2.2 Preserve existing required-owner and uploaded-file fail-closed gates.

## 3. Regression Coverage

- [x] 3.1 Add an inline-image, prompt-cache-affined route-level regression
  that receives a `429` on the first account and completes on the second.

## 4. Verification

- [x] 4.1 Run the focused regression and adjacent stream retry coverage.
- [x] 4.2 Run Ruff check/format, ty, architecture validation, and strict
  change validation. Record any pre-existing aggregate-spec validation failures.
- [x] 4.3 Inspect the final diff and worktree status for scope and unrelated
  changes.
