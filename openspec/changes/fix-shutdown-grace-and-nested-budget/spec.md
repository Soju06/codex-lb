## Shutdown settlement grace

Terminal websocket settlement MUST use the unused post-drain cleanup reserve after the shared drain deadline expires. The settlement wait MUST be bounded by the remaining combined drain-plus-reserve deadline.

## Nested cleanup budget

Lifespan cleanup MUST pass no more than the current remaining drain timeout to nested persistence cleanup. The nested timeout MUST NOT exceed the containing shutdown budget.

## Drain timeout validation

`shutdown_drain_timeout_seconds` MUST be greater than zero and MUST NOT exceed 300 seconds.
