# Tasks

## Specification

- [x] Add the focused retry-circuit delta and context with the process-local vs
  durable/replica-wide ownership boundary.
- [x] Validate the change and all main specs with strict OpenSpec tooling (or
  record the unavailable validator and equivalent checks).

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

- [x] Run affected unit/integration tests, Ruff, formatting, `ty`, architecture,
  diff checks, and exact-head Standards/Input reviews.
