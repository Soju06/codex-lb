## ADDED Requirements

### Requirement: Stopping the account-deletion worker does not consume a worker interval

`AccountDeletionScheduler.stop()` MUST end the worker's idle interval wait
without depending on cancellation delivery: it MUST set the wake signal before
cancelling the loop task, and the loop MUST exit on its stop gate rather than
running another deletion pass. Cancellation alone is not sufficient, because the
tick's own session teardown absorbs the single `CancelledError` that `stop()`
raises: when a tick's query fails, `get_background_session` runs `_safe_rollback`
(`app/db/session.py`) in the loop's own frame, and the safe-teardown helpers
there discard a cancellation that lands inside them — the bounded SQLite wait
drops it outright, the unbounded wait re-raises it into
`except BaseException: return`. After such a swallow the loop would park for a
full `DELETION_INTERVAL_SECONDS` before re-reading the stop gate. That park
exceeds the shutdown drain timeout and the owned launcher's lifespan cleanup
bound on its own, so shutdown would escalate to termination.

#### Scenario: Stop cancellation is absorbed by the running tick
- **GIVEN** the account-deletion worker is running a tick whose body swallows cancellation
- **WHEN** shutdown stops the worker
- **THEN** the loop's next interval wait returns immediately and the loop exits on its stop gate
- **AND** `stop()` returns without waiting out the worker interval

#### Scenario: Stop lands while the worker is idle in its interval wait
- **WHEN** shutdown stops the account-deletion worker while it is parked in its interval wait
- **THEN** the loop task ends promptly and the released wait does not start another deletion pass
