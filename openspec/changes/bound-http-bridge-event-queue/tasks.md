## 1. Regression

- [x] 1.1 Add `test_http_bridge_live_event_queue_applies_backpressure` through actual request preparation and upstream event relay

- [x] 1.2 Capture deterministic RED showing the unbounded queue lets a paused-consumer producer complete, while the paced control remains ordered


## 2. Bounded delivery

- [x] 2.1 Construct live HTTP-bridge event queues with the two-event terminal-safe internal capacity

- [x] 2.2 Release full-queue producer waits on downstream detachment without changing persistence or terminal settlement ownership

- [x] 2.3 Preserve completed durable replay with finite transcript-sized startup buffering

- [x] 2.4 Prove resumed and paced delivery order, terminal end marker, disconnect cancellation, settlement, and task cleanup

- [x] 2.5 Account retained live payload bytes in one fixed process-wide budget; revoke a queue when a reservation cannot be made and release bytes on dequeue


## 3. Verification

- [x] 3.1 Run the exact regression, focused bridge lifecycle tests, and relevant broader proxy tests

- [x] 3.2 Run changed-file diagnostics, Ruff, type checks, and strict OpenSpec validation

- [x] 3.3 Run an actual-path async surface driver and record bounded-pressure plus resumed-delivery output

- [x] 3.4 Review the committed diff for disconnect/cancellation, task ownership, terminal settlement, durable spool/replay, and async task leaks

- [x] 3.5 Test cross-session budget pressure, byte release after dequeue, and fail-closed queue revocation without a new operator setting


## 4. Maintainer review follow-up

- [x] 4.1 Reproduce the timeout-grace event loss, make queue-read cancellation non-consuming, and return a finished reconciled read

- [x] 4.2 Publish attached failure terminals without waiting for live capacity and prove a later session lifecycle waiter is not blocked by a stalled consumer

- [x] 4.3 Remove the unused denied-anchor generation capture helper

- [x] 4.4 Run focused and broader bridge tests, Ruff, formatting, type checks, proxy architecture validation, diff checks, strict targeted OpenSpec validation, and final exact-candidate review


## 5. Current-head review follow-up

- [x] 5.1 Add product-path regressions for budget exhaustion after the first SSE event and repeated cancellation during blocked-put cleanup

- [x] 5.2 Keep post-commit budget failure inside SSE and defer blocked-put cancellation until task reaping and reservation release finish

- [x] 5.3 Run focused and bridge integration tests, Ruff, formatting, type checks, architecture validation, diff checks, strict targeted OpenSpec validation, and exact-candidate review

## 6. Current-head reconciliation

- [x] 6.1 Bound a full live-queue enqueue by the existing bridge request deadline and preserve sibling reader settlement on expiry

- [x] 6.2 Preserve delayed-generator terminal delivery while discarding only explicitly abandoned queues

- [x] 6.3 Keep HTTP-bridge direct and routed WebSockets off native egress while preserving the native default elsewhere

- [x] 6.4 Add deadline, terminal append-exception, and direct/routed native-bypass regressions and rerun the affected proof lanes

## 7. Round-19 performance and proof

- [x] 7.1 Measure producer-ahead and interleaved queue delivery against pinned main and the delivered PR head
- [x] 7.2 Replace read-side task races with owned futures and same-task timeout cancellation; prove no child tasks and retained raced payloads
- [x] 7.3 Exercise real shared-reader dispatch and sibling deadline settlement after the paused enqueue reaches its own deadline
- [x] 7.4 Clarify the HTTP-bridge native-egress exception and record residual costs without claiming native flow control
- [x] 7.5 Record immutable benchmark evidence and run affected local verification
- [x] 7.6 Verify delayed-terminal delivery at submit, cooldown, and registration waits in both response modes; run a truncation-producing red control
- [ ] 7.7 Obtain maintainer acceptance of the native fallback and residual performance cost before merge
