# Tasks

## Specification

- [x] Add the focused retry-circuit delta and context with the process-local vs
  durable/replica-wide ownership boundary.
- [x] Strictly validate this change and the owning Responses spec, pass
  canonical validation for all main specs, and record the exact validation
  scope and any unrelated baseline strict-validation failures in context.md.

## Implementation

- [x] Normalize elapsed durable cooldowns to the zero sentinel without clearing
  equal/newer active local half-open leases.
- [x] Record the owning session, return only that probe as an elapsed cooldown,
  fence each release by session/token/deadline/process-local generation,
  retain live owners past the default lease through `bridge_request_deadline`,
  fence late completion by durable episode plus local generation, classify
  explicitly proven proxy continuity loss as neutral, and preserve genuine
  upstream strikes.
- [x] Order proxy continuity reset lifecycle ownership, detach, disarm, release,
  settlement, and close through cancellation-safe cleanup; retain failed
  account-lease handles for explicit retry after transport close.
- [x] Apply the same detach/disarm-before-release/close ordering to an in-place
  reconnect whose required continuity owner is unavailable, preserve its typed
  owner error when selected-account lease cleanup fails, and keep retained
  account-release retries single-flight.

## Coverage

- [x] Preserve a positive elapsed durable cooldown as a one-shot local
  half-open transition, prove concurrent admission is single-flight, and keep
  the consumed marker for the lifetime of an ever-claimed durable row while
  absent, zero, and negative deadlines retain unrestricted zero-sentinel
  behavior.
- [x] Cover elapsed/absent rows, real expiry single-flight, owner fencing,
  equal-version and lookup-failure lease retention, and replica-boundary state.
- [x] Cover proxy continuity teardown ordering, cancellation, continuity
  neutrality (including injected-anchor provenance), stale-generation no-op
  releases, client previous-response no-op accounting, and genuine upstream
  failure through unit and real bridge paths.

## Verification

- [x] Preserve ordinary reconnect selection terminals while retaining ordered
  required-owner cleanup; prove both through the registered reader path.

- [x] Prove real persisted exhausted-owner selection returns the exact probe
  and account lease without alternate dispatch or recovery wait; retain
  transient hard-affinity recovery and complete affected independent reviews.

- [x] Fix and verify reader-origin owner-loss self-cancellation and same-tick
  returned-probe reconciliation with current-candidate independent reviews.

- [x] Reconcile accepted output-free replay with request-bound probe admission,
  gate contention, cancellation, and retained replacement-account cleanup.
- [x] Verify the combined paths, upstream cancellation guard, and exact-candidate
  Standards/Input reviews before updating the PR.

- [x] Integrate the bounded detached-retirement sweep while retaining mandatory
  lifecycle cleanup, live turn ownership, and single-flight retained-lease retry.
- [x] Verify changed AnyIO and cancellation-cascade dependencies with focused
  integration proof, repository guards, and independent integration reviews.

- [x] Reconcile the PR onto upstream timing seams without weakening cancellation,
  probe ownership, retained lease, or live-resource guards.
- [x] Verify injected-clock expiry and scheduler-owned cleanup, sync the delta
  into the main spec, and run timing guards plus integration-scope reviews.

- [x] Cover production admission preregistration under contended-lock level
  cancellation and complete probe handback plus eligible retirement.
- [x] Retry detached replacement-account leases by the lease account, with
  unrelated live sessions preserved.

- [x] Run affected unit/integration tests, Ruff, formatting, `ty`, architecture,
  diff checks, and exact-head Standards/Input reviews.
- [x] Fence stale claimed-probe failure settlement, preserve terminal/account
  cleanup and replacement completion, and verify current/non-probe controls.
- [x] Preserve captured probe generation for continuity returns and the one-shot
  positive elapsed durable transition when reconciling a persisted row.


## Remaining delivery and policy gates

- [x] Reconcile current upstream constants and extract reason-only incomplete classification into an independently based change, with reduced-only and both-order composition proof.
- [ ] Resolve the sibling-failure and later owner-success settlement contract with the maintainer, then implement and verify the accepted rule.
- [ ] Resolve the abandoned-probe expiry investigation or accepted ownership policy without releasing genuinely owned cleanup prematurely.
- [ ] Complete current-head hosted checks and maintainer review before merge; archive only after final verification.
