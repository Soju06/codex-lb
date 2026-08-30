# Tasks

- [x] Carry the post-drain reserve through shutdown state and use it for terminal websocket settlement.
- [x] Bound the lifespan recovery-settlement cleanup by the live drain remainder.
- [x] Validate `shutdown_drain_timeout_seconds` as `1..300`.
- [x] Add fail-pre/pass-post regression coverage for settlement grace, nested budget, and invalid settings.
- [x] Run focused shutdown, websocket, proxy bridge, and OpenSpec validation suites.
