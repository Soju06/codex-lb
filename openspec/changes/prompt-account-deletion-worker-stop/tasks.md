## 1. Prompt worker stop
- [x] 1.1 Reproduce the stall deterministically: a tick body that swallows the stop cancellation leaves `stop()` blocked in `AccountDeletionScheduler._run_loop`'s interval wait for the whole interval.
- [x] 1.2 Release the interval wait from `stop()` without depending on cancellation delivery.
- [x] 1.3 Cover the absorbed-cancel stop, the idle-wait stop (no extra pass), and the surviving `wake()` nudge.
- [x] 1.4 Pass lint, type check, the touched test files, `tests/unit`, and strict OpenSpec validation.
