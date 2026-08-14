## 1. Fix

- [x] 1.1 Restore the pending namespace in `_flush_pending_bumps` when the bump write is cancelled or raises, then re-raise

## 2. Tests

- [x] 2.1 A cancelled write restores the marker, and the marker is cleared before the write (locking in the intended coalescing)
- [x] 2.2 A raising write restores the marker
- [x] 2.3 End-to-end: the running poller retries the aborted namespace and writes its version

## 3. Spec

- [x] 3.1 Make "remains pending" explicitly cover an aborted write, not only a failed one, keeping the requirement normative and the rationale in the proposal
