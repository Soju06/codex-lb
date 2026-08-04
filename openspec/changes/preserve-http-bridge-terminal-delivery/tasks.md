## 1. Implementation

- [x] 1.1 Capture a completed request's event queue while holding the pending lock.
- [x] 1.2 Deliver the completed event and end-of-stream marker through that captured queue.
- [x] 1.3 Preserve detach-first cancellation and retry behavior.
- [x] 1.4 Suppress synthetic idle failure only while completed delivery is actively producing.
- [x] 1.5 Serialize the completed-claim/terminal-timeout decision under the pending lock and atomically revoke the timeout-first queue.
- [x] 1.6 Emit one bounded diagnostic when completed delivery suppresses an idle timeout.
- [x] 1.7 Keep a completed claim authoritative after terminal delivery is queued but before the stream consumes it.

## 2. Regression coverage

- [x] 2.1 Add a stream-level regression for slow bookkeeping after completed pending removal.
- [x] 2.2 Verify the regression fails on the pre-fix implementation and passes after the fix.
- [x] 2.3 Verify failed completed bookkeeping releases timeout suppression.
- [x] 2.4 Add deterministic timeout-first and completed-first race regressions, including one-time suppression logging.
- [x] 2.5 Add a regression where completed delivery finishes while the timeout path awaits pre-response recovery.

## 3. Validation

- [x] 3.1 Run focused HTTP bridge tests and changed-file Ruff checks.
- [x] 3.2 Run strict OpenSpec validation.
- [x] 3.3 Re-run focused/full HTTP bridge tests, static checks, architecture checks, strict OpenSpec validation, and local Codex review.
