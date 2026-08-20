## Context

HTTP-bridge request preparation currently attaches `asyncio.Queue(maxsize=0)` to every live downstream stream. The upstream reader awaits each `put`, but an unbounded queue makes that await non-blocking regardless of downstream pace. The same request state also carries terminal events and byte-bounded durable transcript replay, and downstream detachment revokes the mutable queue while the shared upstream reader continues draining the request to terminal settlement.

## Goals / Non-Goals

**Goals:**

- Bound unread live events per HTTP-bridge request and propagate pressure through the existing awaited upstream-reader enqueue.
- Preserve event order, terminal/end-of-stream delivery, reservation and request-log settlement, durable spool/replay, and shared-reader cleanup.
- Make paused, resumed, paced, and detached-consumer behavior deterministic in focused integration coverage.

**Non-Goals:**

- Add an operator setting or reuse the request-admission queue limit for a different unit.
- Change bridge admission, wire events, durable spool byte limits, retry/failover, or account selection.
- Refactor the broader HTTP-bridge lifecycle.

## Decisions

1. **Use a two-event internal live queue.** Request preparation will construct the live queue with capacity for exactly a terminal frame plus its end-of-stream marker. Local startup failures enqueue that pair before the downstream consumer loop begins, so a capacity of one would deadlock submission. The third unread live event blocks the producer; a larger arbitrary count would multiply the existing 16 MiB per-event ceiling without a contract-derived benefit. This is internal flow control rather than an operator policy, so no setting is added.

2. **Make queue revocation unblock a pressured producer.** A request-scoped event will signal that downstream ownership was revoked. Enqueue uses `put_nowait` on the fast path and races only a full-queue `put` against revocation. Detachment signals revocation before continuing terminal drain, so a disconnected consumer cannot strand the shared upstream reader or an enqueue task. Terminal settlement and durable persistence continue even when downstream delivery is skipped.
3. **Size completed durable replay to its already-loaded transcript.** Durable replay is byte-bounded before retrieval and is loaded before the live consumer loop starts. When replay is selected, replace the live queue with a finite queue sized exactly for the retrieved events plus end-of-stream, then enqueue synchronously. This avoids startup deadlock without turning live buffering back into an unbounded queue.
4. **Keep terminal ordering unchanged.** Existing producer call sites retain their event-then-end-marker order. The enqueue helper changes waiting behavior only; it does not reorder, drop while attached, alter spool persistence, or transfer settlement ownership.

Alternatives rejected:

- Reusing `http_responses_session_bridge_queue_limit`: it counts admitted requests, not event memory, and would couple unrelated units.
- Reusing durable spool pending-event settings: they govern asynchronous database batching, not downstream live delivery.
- An unbounded queue plus metrics or dropping: neither applies backpressure nor preserves complete Responses streams.
- Replacing the queue with a new channel abstraction: broader than required and riskier across the mature terminal/replay paths.

## Risks / Trade-offs

- **[Head-of-line pressure on the shared upstream socket]** → The response-create gate already serializes active turns; pressure is intentional and bounded to the slow downstream that owns the active response.
- **[Disconnect while a producer awaits capacity]** → Queue revocation wins the enqueue race and all spawned wait tasks are cancelled and awaited.
