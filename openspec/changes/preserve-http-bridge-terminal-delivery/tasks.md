## 1. Implementation

- [x] 1.1 Capture a completed request's event queue while holding the pending lock.
- [x] 1.2 Deliver the completed event and end-of-stream marker through that captured queue.
- [x] 1.3 Preserve detach-first cancellation and retry behavior.
- [x] 1.4 Suppress synthetic idle failure only while completed delivery is actively producing.

## 2. Regression coverage

- [x] 2.1 Add a stream-level regression for slow bookkeeping after completed pending removal.
- [x] 2.2 Verify the regression fails on the pre-fix implementation and passes after the fix.
- [x] 2.3 Verify failed completed bookkeeping releases timeout suppression.

## 3. Validation

- [x] 3.1 Run focused HTTP bridge tests and changed-file Ruff checks.
- [x] 3.2 Run strict OpenSpec validation.
