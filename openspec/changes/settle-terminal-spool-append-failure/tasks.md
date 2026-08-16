## 1. Regression

- [x] 1.1 Add a deterministic unit regression proving a terminal append exception settles the operation through the unchanged owner fence.
- [x] 1.2 Capture the focused regression failing before production code changes.

## 2. Implementation

- [x] 2.1 Add the minimum fenced fallback settlement for terminal append exceptions without claiming spool completeness.
- [x] 2.2 Add a production-repository process/recovery proof that a reconnect cannot observe the operation as acknowledged.

## 3. Verification

- [x] 3.1 Capture focused GREEN and run adjacent HTTP-bridge unit and integration tests.
- [x] 3.2 Run changed-file formatting, lint, type diagnostics, strict OpenSpec validation, and package/build checks required by the repository.
- [x] 3.3 Review the committed diff independently and address every in-scope finding.
