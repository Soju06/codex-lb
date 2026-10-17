## Context

The bridge lookup loop first resolves a reusable previous-response session and records it in `session_to_return_after_close`. The generic create arm is selected independently by `inflight_future is None`, so it can register a new pending future for the already-resolved key before the function returns the reused session. The existing cleanup/janitor intentionally removes only completed futures and is covered by a unit test.

## Goals / Non-Goals

**Goals:**

- Make the reuse decision terminal for session creation in that loop.
- Leave no unresolved future registered for a key whose existing session is returned.
- Preserve all create, waiter, handoff, timeout, and janitor behavior for paths that do not reuse a previous-response session.

**Non-Goals:**

- Do not change janitor eligibility or restart-blocking semantics for genuinely live creation futures.
- Do not redesign durable ownership, session closing, or response routing.

## Decisions

Guard the generic `inflight_future is None` creation arm with `session_to_return_after_close is None`. This is the smallest local invariant: once reuse has selected a session, the loop may still close detached sessions, then returns the selected session without publishing a creation future. An early `continue` or future resolution would add lifecycle behavior without benefit and could interfere with the existing create-chain arms.

The regression uses the real `_get_or_create_http_bridge_session` previous-response lookup path and asserts both registry state and a second successful reuse. The existing janitor test remains unchanged as a negative control.

## Risks / Trade-offs

- [Risk] A future branch might set `session_to_return_after_close` for a case that still needs creation. → Mitigation: the symbol has one assignment, in the validated live-session reuse arm; all other arms leave it `None` and retain the original create condition.
- [Risk] A future remains from an earlier concurrent creator. → Mitigation: reuse already requires the canonical previous-key inflight lookup to be empty; waiter behavior remains guarded by `inflight_future is not None`.
