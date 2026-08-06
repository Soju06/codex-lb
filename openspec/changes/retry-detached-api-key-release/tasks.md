## 1. Regression

- [x] 1.1 Add a real-repository regression that injects one finalize failure and one fallback-release failure.
- [x] 1.2 Confirm the regression fails deterministically twice on baseline `3fe0d6f286019a0505783d803db9a1d8cdf6b307`.

## 2. Implementation

- [x] 2.1 Keep the fallback release tracked while retrying persistence failures with capped backoff.
- [x] 2.2 Preserve idempotent settlement, cancellation ownership, and truthful persistence-drain behavior.
- [x] 2.3 Bound concurrent fallback repository attempts to four with one shared per-service gate.

## 3. Verification

- [x] 3.1 Run the focused detached-settlement and API-key reservation tests.
- [x] 3.2 Run changed-file Ruff, format, type, proxy-architecture, and strict OpenSpec checks.
- [x] 3.3 Inspect the final diff and worktree status for scope and unrelated changes.
- [x] 3.4 Add deterministic fan-out coverage for the shared retry concurrency bound.
