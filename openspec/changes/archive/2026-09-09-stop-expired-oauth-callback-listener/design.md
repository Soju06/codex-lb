## Context

Pending browser flows already carry a TTL. Local pruning and serialized listener shutdown already exist, but neither runs when a deadline passes without a later request.

## Goals / Non-Goals

- Release an abandoned listener without a follow-up request.
- Keep the shared listener available until the last overlapping browser flow expires.
- Cover pending flows hydrated from shared storage and drain scheduled work on reset.
- Do not change listener startup, terminal persistence, reset semantics, Docker defaults, APIs, or database schemas.

## Decisions

Keep one task and one wake-up event on the process-local store. The task computes the earliest pending browser deadline under the store lock. A new or hydrated browser flow wakes it to recalculate, including when hydration introduces an earlier deadline. At a timeout it invokes the existing `_stop_callback_server_if_idle()`, which prunes due flows and serializes listener shutdown. It then recomputes the next deadline.

Arm the task after browser listener startup and when durable reconciliation hydrates pending browser state. Full store reset clears flows, cancels the task, and awaits its exit. The task clears its own slot under the store lock before retiring. Timer exceptions are logged rather than becoming unobserved task failures.

## Risks / Trade-offs

Wall-clock changes and early wakes require deadline recalculation. Overlapping starts and terminal callbacks retain the existing idle check and stop serialization. This patch deliberately adds no generations, startup supervision, terminal-transition tasks, or shutdown retries.

Docker reserves a published host port independently of the in-container listener. Removing that mapping changes automatic browser callback behavior and the Windows helper contract, so it belongs in a separate deployment proposal.

## Migration Plan

No migration or configuration change is required.
