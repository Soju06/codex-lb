## Context

The terminal HTTP-bridge event path intentionally skips a separate operation-state update because `append_terminal_operation_event` normally stores the terminal event and authoritative state atomically. If that repository call raises, the batcher currently logs and returns `False`; the durable row can therefore remain `acknowledged` with an incomplete spool after the terminal event was delivered downstream.

The durable bridge already exposes `update_operation` with the same operation, session, instance, and owner-epoch fence. The bounded-spool overflow path uses that method to preserve authoritative settlement without claiming replay completeness.

## Goals / Non-Goals

**Goals:**

- Preserve an authoritative terminal operation state after a terminal append exception.
- Apply the existing session and owner-epoch fence to fallback settlement.
- Keep the incomplete spool ineligible for transcript replay.
- Prove the result through the production repository/coordinator seam.

**Non-Goals:**

- Drain queued events during graceful shutdown.
- Change successful terminal event persistence or replay eligibility.
- Change warmup, upstream delivery, retry policy, or public response shapes.

## Decisions

- On `append_terminal_operation_event` exception, call `update_operation` with the same operation ID, session ID, instance ID, owner epoch, intended terminal state, and response ID. This reuses the authoritative fenced repository operation rather than adding another persistence mechanism.
- Return `False` after fallback settlement because the terminal transcript is still incomplete. Recovery may observe the terminal outcome but must not replay a transcript whose terminal event was not durable.
- Log a rejected fence or fallback exception and do not re-raise. The terminal event has already been selected for downstream delivery, so bookkeeping failure must not replace that event.
- Do not invoke fallback for ordinary `False` returns. The repository's bounded-spool overflow path already settles terminal state atomically, while a false owner fence must not be bypassed.

## Risks / Trade-offs

- [A transient database failure can affect both append and fallback update] -> Preserve the existing non-blocking terminal path and emit a warning for operator diagnosis.
- [A stale owner could attempt to settle another owner's operation] -> Pass the unchanged session/instance/epoch fence and treat rejection as non-settlement.
- [A failed terminal append leaves no replayable terminal event] -> Keep `event_spool_complete` false and return `False`; authoritative state and transcript completeness remain separate facts.
