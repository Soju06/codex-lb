## Context

The terminal HTTP-bridge event path intentionally skips a separate operation-state update because `append_terminal_operation_event` normally stores the terminal event and authoritative state atomically. If that repository call raises, the batcher currently logs and returns `False`; the durable row can therefore remain `acknowledged` with an incomplete spool after the terminal event was delivered downstream.

The durable bridge exposes operation settlement under the same operation, session, instance, and owner-epoch fence. Fallback additionally needs to identify the acknowledged terminal attempt so a delayed write cannot overwrite a newer retry admitted under the same owner epoch.

## Goals / Non-Goals

**Goals:**

- Preserve an authoritative terminal operation state after a terminal append exception.
- Apply the existing session and owner-epoch fence plus an acknowledged-state and response-identity comparison to fallback settlement.
- Keep the incomplete spool ineligible for transcript replay.
- Prove the result through the production repository/coordinator seam.

**Non-Goals:**

- Drain queued events during graceful shutdown.
- Change successful terminal event persistence or replay eligibility.
- Change warmup, upstream delivery, retry policy, or public response shapes.

## Decisions

- On `append_terminal_operation_event` exception, return an incomplete append result that explicitly requires fallback settlement. The relay queues the selected terminal SSE block and end-of-stream marker before awaiting a dedicated conditional settlement with the same operation ID, session ID, instance ID, owner epoch, immutable recovery-dispatch generation, intended terminal state, persisted upstream response IDs, and client-visible response ID. The repository accepts only that acknowledged attempt or its already-committed terminal result, without keeping the terminal event behind a stalled fallback write.
- Keep append and fallback settlement structured in the relay task instead of detaching them. This bounds settlement concurrency to active relay operations, and the relay defers cancellation until the append and any required settlement finish before preserving the cancellation outcome.
- Force `event_spool_complete=false` in the same conditional fallback update. This keeps replay disabled even when terminal append committed but its commit acknowledgement was lost before the caller observed success.
- Log a rejected fence or fallback exception inside the batcher's settlement method and do not re-raise. The terminal event has already been queued for downstream delivery, so bookkeeping failure must not replace or delay that event.
- Do not invoke fallback for ordinary `False` returns. The repository's bounded-spool overflow path already settles terminal state atomically, while a false owner fence must not be bypassed.

## Risks / Trade-offs

- [A transient database failure can affect both append and fallback update] -> Queue the terminal event and end-of-stream marker before awaiting structured fallback settlement and emit a warning for operator diagnosis.
- [Relay cancellation can interrupt terminal append or the delivery-authority claim] -> Defer cancellation through append, delivery ownership, and any required fallback; mark completed delivery authoritative before preserving cancellation.
- [A grouped terminal fan-out can release owner authority too early or stall on its first fallback] -> Start every owner-fenced append concurrently and await all append outcomes before exposing any sibling queue, then queue every sibling terminal event and end-of-stream marker before fallback settlement, settle every sibling before finalization, continue later finalizers after one fails, and preserve cancellation as the final outcome.
- [A stale owner could attempt to settle another owner's operation] -> Pass the unchanged session/instance/epoch fence and treat rejection as non-settlement.
- [A delayed append or fallback could overwrite a newer retry under the same owner] -> Require the prior attempt's immutable recovery-dispatch generation plus acknowledged/terminal state and response identity in both persistence predicates.
- [Replay aliases can differ from the upstream response identity persisted at acknowledgement] -> Carry both the active upstream identity and retained replay identity as possible CAS values separately from the client-visible terminal identity, covering a failed replacement-acknowledgement write, and preserve the known identity when no new client-visible identity is supplied.
- [A failed terminal append leaves no replayable terminal event] -> Keep `event_spool_complete` false and report `persisted=false`; authoritative state and transcript completeness remain separate facts.
