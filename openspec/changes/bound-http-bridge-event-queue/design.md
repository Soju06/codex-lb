## Context

HTTP-bridge request preparation currently attaches `asyncio.Queue(maxsize=0)` to every live downstream stream. The upstream reader awaits each `put`, but an unbounded queue makes that await non-blocking regardless of downstream pace. The same request state also carries terminal events and byte-bounded durable transcript replay, and downstream detachment revokes the mutable queue while the shared upstream reader continues draining the request to terminal settlement. A bounded queue must not turn one paused consumer into an unbounded shared-reader stall: the current implementation releases its full-queue enqueue only at the request deadline. The accepted delivery-stall requirement below remains unimplemented.

## Goals / Non-Goals

**Goals:**

- Bound unread live events per HTTP-bridge request and propagate pressure through the existing awaited upstream-reader enqueue.
- Preserve event order, terminal/end-of-stream delivery, reservation and request-log settlement, durable spool/replay, and shared-reader cleanup.
- Make paused, resumed, paced, and detached-consumer behavior deterministic in focused integration coverage.
- Keep a delayed live generator's revoked queue until its selected terminal
  event and end marker are delivered, while allowing an explicitly abandoned
  queue to release retained bytes.
- Keep HTTP-bridge upstream WebSockets on the existing non-native adapter until
  native per-stream flow control exists; do not change native queue semantics
  for unrelated transports.

**Non-Goals:**

- Add an operator setting or reuse the request-admission queue limit for a different unit.
- Change bridge admission, wire events, durable spool byte limits, retry/failover, or account selection.
- Refactor the broader HTTP-bridge lifecycle.

## Decisions

1. **Keep the current two-event live queue.** The third unread live event blocks the producer. Terminal event/EOS now use a separate ordered slot, so they no longer justify this capacity. The round-20 producer-task change preserves capacity and the 256 MiB process budget to isolate its performance effect. A different capacity or the numeric maximum continuous delivery stall remains an explicit owner decision; raising finite capacity alone would not bound a stalled consumer's eventual wait.

2. **Make queue revocation unblock a pressured producer.** Enqueue uses `put_nowait` on the fast path. A full queue awaits a capacity future in the producer task, holding its byte reservation locally. Dequeue, revocation, discard, and terminal publication wake those futures without inserting a payload on another task's behalf. The producer rechecks capacity and revocation, then transfers its reservation synchronously or releases it in synchronous cleanup. Cancellation cannot interrupt that cleanup with another await. Dequeue likewise removes its item in the consumer task after a non-consuming wakeup. The existing monotonic request deadline still bounds the full-queue wait; expiry preserves a delivery failure and returns control to the shared reader rather than raising through reader failure handling. Terminal settlement and durable persistence continue even when downstream delivery is skipped.
3. **Apply one fixed process-wide byte budget.** Every retained string payload reserves its UTF-8 byte length from a fixed internal 256 MiB envelope before entering a live queue; dequeue releases the reservation. This envelope covers live queued payloads and producer-owned blocked payloads, not aggregate completed replay buffers or total process memory. The envelope is deliberately not a setting: queue capacity and upstream payload limits are implementation safety rules. If a reservation cannot be made, that queue revokes producers and fails closed while an attached stream emits its one explicit `response.failed`/`upstream_unavailable` terminal result. A route may translate that result to HTTP 503 only before its first emitted event; after the response is committed, the failure remains an SSE terminal. Later upstream failure publication is allowed to no-op on the revoked queue. A queue retained by a live delayed generator remains available for the selected terminal failure and end marker; only explicit detachment or another proof of no downstream owner permits terminal cleanup to discard it. Durable persistence, settlement, and cleanup continue in every case. The pressure is logged with sizes, without exposing payload content or adding a retry loop.
4. **Size completed durable replay to its already-loaded transcript.** Durable replay is byte-bounded before retrieval and is loaded before the live consumer loop starts. When replay is selected, replace the live queue with a finite queue sized exactly for the retrieved events plus end-of-stream, then enqueue synchronously. This avoids startup deadlock without turning live buffering back into an unbounded queue.
5. **Keep terminal ordering unchanged without blocking cleanup.** Ordinary upstream producer call sites retain their event-then-end-marker order. Failure finalization uses the live queue's existing out-of-band terminal sequence for attached consumers, so it never waits for a stalled consumer while holding session lifecycle ownership. The consumer drains the bounded live deque before the terminal failure and end marker. Queue-full backpressure therefore remains no-drop for an attached consumer, while fail-closed byte-budget revocation has the explicit terminal contract in the change spec.

Alternatives rejected:

- Per-read item, terminal, budget, and timeout tasks: interleaved delivery exposed task fanout even with a buffered fast path. Empty reads now await an owned future in the consumer task. Publication wakes that future without consuming; the reader removes the item only after its await returns. An `asyncio.timeout` scope cancels that same read, and synchronous reconciliation keeps a raced queued payload available without a grace task.
- Per-put enqueue, revocation, and cleanup tasks: burst delivery repeatedly reaches the finite queue's blocked path. Producer-owned futures remove this task fanout while keeping the same capacity and byte budget.
- Reusing `http_responses_session_bridge_queue_limit`: it counts admitted requests, not event memory, and would couple unrelated units.
- Reusing durable spool pending-event settings: they govern asynchronous database batching, not downstream live delivery.
- An unbounded queue plus metrics or dropping: neither applies backpressure nor preserves complete Responses streams.
- Replacing the queue with a new channel abstraction: broader than required and riskier across the mature terminal/replay paths.

## Risks / Trade-offs

The timing integration uses upstream's injected clock and scheduler for
enqueue deadlines and surrounding lifecycle work. Queue reads and puts create
no child tasks; their futures represent readiness, not timers. `Scheduler.timeout` is an
alias of `asyncio.timeout` in production and uses the existing virtual timeout
implementation in simulation. Positive `wait_for` waits are inline on the
supported Python versions, but nonpositive waits have different scheduling
semantics. Keeping a timeout context preserves the existing zero-deadline
publication race and never adds a read child task. Required-keyword entries
make missing collaborator propagation fail the timing check.

- **[Head-of-line pressure on the shared upstream socket]** → The existing request deadline is the current fallback, not accepted availability proof. The pending per-request delivery-stall bound must release the reader while a same-session sibling can still progress.
- **[Disconnect while a producer awaits capacity]** → Queue revocation wakes the waiting producer; it releases its local reservation without enqueueing.
- **[Repeated cancellation during producer cleanup]** → Reservation release is synchronous and has no cancellation point.
- **[Concurrent sessions consume the process envelope]** → A failed reservation revokes only the affected queue; already queued payloads remain accounted until consumed, so pressure cannot be hidden by clearing accounting early.
- **[Native egress can still block globally]** → HTTP-bridge upstream WebSockets opt out of native egress until native per-stream credits and cancellation exist; unrelated native transport behavior remains unchanged.
- **[Residual CPU cost]** → Byte accounting and cancellation-safe blocked puts still cost CPU. The benchmark separates producer-ahead reads, interleaved empty reads, and bursts. Unbounded main accumulates a whole burst while this queue applies pressure, so burst timings are not equivalent memory behavior.
- **[Shorter sibling deadlines]** → The paused request's own deadline releases its enqueue. This does not independently settle a shorter sibling deadline while the shared reader remains blocked.

Round 20 confirms the request budget defaults to 7200 seconds. No existing
setting defines tolerated SSE delivery stall: stream idle governs upstream
silence, keepalive controls emission cadence, and WebSocket idle governs a
different transport state. The maintainer has accepted bounded per-request delivery-stall termination,
ordered retained-prefix delivery followed by failure/EOS, reservation release,
and same-session sibling progress. Only the numeric maximum continuous stall
remains undecided within that outcome. Native transport and performance
acceptance remain separate. See [accepted scope and proof plan](accepted-stall-contract.md).
