## 1. Regression

- [x] 1.1 Add `test_http_bridge_live_event_queue_applies_backpressure` through actual request preparation and upstream event relay

- [x] 1.2 Capture deterministic RED showing the unbounded queue lets a paused-consumer producer complete, while the paced control remains ordered


## 2. Bounded delivery

- [x] 2.1 Construct live HTTP-bridge event queues with the two-event terminal-safe internal capacity

- [x] 2.2 Release full-queue producer waits on downstream detachment without changing persistence or terminal settlement ownership

- [x] 2.3 Preserve completed durable replay with finite transcript-sized startup buffering

- [x] 2.4 Prove resumed and paced delivery order, terminal end marker, disconnect cancellation, settlement, and task cleanup


## 3. Verification

- [x] 3.1 Run the exact regression, focused bridge lifecycle tests, and relevant broader proxy tests

- [x] 3.2 Run changed-file diagnostics, Ruff, type checks, and strict OpenSpec validation

- [x] 3.3 Run an actual-path async surface driver and record bounded-pressure plus resumed-delivery output

- [ ] 3.4 Review the committed diff for disconnect/cancellation, task ownership, terminal settlement, durable spool/replay, and async task leaks
