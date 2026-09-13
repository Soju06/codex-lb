# Tasks

## Specification

- [x] Add the `responses-api-compat` delta for retiring sessions rejected by
      hard-key retry-circuit cooldown before upstream dispatch.
- [x] Record the late-submit and startup pre-submit scenarios, including the
      replay bypass and the owned-session safeguards (concurrent admitted
      probe, pending work, foreign handoff).

## Implementation

- [x] Mark and retire the session in the late submit suppression branch when
      no other turn owns it (shared idle check under `pending_lock`).
- [x] Mark and retire the session in the startup continuity cooldown terminal
      branch under the same ownership check.
- [x] Register the submit as an admission waiter from submit entry, hand the
      registration over at dispatch, and release it on every pre-dispatch
      exit (re-running deferred retirement).
- [x] Keep proof-gated and operation-fenced bypasses unchanged.

## Regression coverage

- [x] Assert late suppression returns the same 503, marks the session retiring,
      invokes the bounded retire helper, and never sends upstream.
- [x] Assert startup pre-submit suppression marks the session retiring, invokes
      the helper, preserves the 503 envelope, and never submits.
- [x] Assert the replay bypass path does not retire the session.
- [x] Assert late suppression of an idle, never-dispatched session closes it
      through the real drain-retirement helper (`retire_after_drain` bounded close).
- [x] Assert a deterministic A/B interleaving (A admitted as the half-open
      probe and suspended in the gate, B suppressed by A's lease) leaves the
      session unmarked and unclosed and lets A reach dispatch.
- [x] Assert late and startup suppression leave a session owned by a
      registered admission waiter unmarked.
- [x] Assert a submit is counted as an admission waiter inside the gate and
      releases that registration on a pre-dispatch failure, running the
      retirement it deferred exactly once.

## Verification

- [x] Run focused and full HTTP bridge unit/integration tests and Ruff on
      changed files.
- [x] Run strict OpenSpec validation and inspect the final diff/status.

Validation note: the change passes strict OpenSpec validation. The repository's
non-strict full spec scan reports one pre-existing `model-source-routing`
failure; the affected `responses-api-compat` and `proxy-admission-control`
specs pass.
