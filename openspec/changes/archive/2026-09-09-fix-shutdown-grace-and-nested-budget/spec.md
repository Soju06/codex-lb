## Shutdown settlement grace

Terminal websocket settlement MUST use the unused post-drain cleanup reserve after the shared drain deadline expires. The settlement wait MUST be bounded by the remaining combined drain-plus-reserve deadline.

## Nested cleanup budget

Lifespan cleanup MUST bound every nested persistence cleanup by a live remaining deadline, never by the full configured drain timeout. Recovery-settlement cleanup runs after the drain barrier and MUST use the remaining combined drain-plus-reserve deadline, so an exhausted drain does not leave it a zero budget; persistence cleanup after bridge teardown MUST use the remaining drain timeout. No nested timeout MUST exceed the containing shutdown budget.

## Drain timeout validation

`shutdown_drain_timeout_seconds` MUST be greater than zero and MUST NOT exceed 300 seconds.
