## 1. Reproduce and fix

- [x] 1.1 Reproduce the split-row poisoned-anchor loop at both authenticated native HTTP routes and record baseline failure.
- [x] 1.2 Load the supported fallback-key circuit before anchor planning; verify full-resend recovery without trim and later continuation.
- [x] 1.3 Verify delta-only refusal, explicit-anchor preservation, API-key scope, and sibling isolation with route regressions.

## 2. Validate and submit

- [x] 2.1 Run affected transport/circuit tests and lint/type/architecture checks; record environmental limits.
- [x] 2.2 Sync requirements/context, strictly validate OpenSpec, verify coverage and archive the change.
- [x] 2.3 Commit and prepare a focused PR with reproduction and cloud-validation limits.
