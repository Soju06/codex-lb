# responses-api-compat Delta

## ADDED Requirements

### Requirement: Aborted terminal bookkeeping settles claimed reservations exactly once

The HTTP bridge MUST settle a request's API-key reservation exactly once even
when terminal-event bookkeeping aborts after removing the request from pending
ownership; that bookkeeping continuation exclusively owns the settlement. If
the continuation raises or is cancelled before finalization transfers that
settlement, the abort path MUST settle every request it still owns: the
reservation heartbeat MUST be cancelled, the
reservation MUST be released, and the downstream waiter SHOULD be unblocked
with an end-of-stream marker instead of waiting for its idle timeout. The
abort settlement MUST run to completion under cancellation (shielded), MUST
apply to the grouped previous-response error path's not-yet-finalized
remainder, and MUST NOT settle requests that a retry branch restored to
pending ownership. Settlement MUST remain idempotent so an abort overlapping
an already-transferred finalization cannot double-account usage.

If the abort settlement itself fails, the claim MUST be marked abandoned and
request detachment MUST be allowed to reclaim that settlement even though the
request is no longer in pending ownership. Detachment MUST NOT settle a live
claim whose bookkeeping continuation is still running.

#### Scenario: Completed bookkeeping raises after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** later completed bookkeeping raises before finalization
- **THEN** the reservation heartbeat task finishes
- **AND** the API-key reservation is released exactly once
- **AND** no reservation heartbeat touch runs afterward

#### Scenario: Completed bookkeeping is cancelled after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** the bookkeeping continuation is cancelled before finalization
- **THEN** the shielded abort settlement still cancels the heartbeat and releases the reservation
- **AND** the cancellation is re-raised after settlement

#### Scenario: Grouped previous-response finalization aborts mid-loop

- **GIVEN** a grouped previous-response error has removed multiple requests from pending ownership
- **WHEN** finalization aborts after settling only a prefix of those requests
- **THEN** every not-yet-finalized request in the group has its heartbeat cancelled and its reservation released

#### Scenario: Detachment reclaims an abandoned claim

- **GIVEN** terminal bookkeeping claimed a request out of pending ownership, aborted, and its abort settlement failed
- **WHEN** the downstream stream detaches that request
- **THEN** detachment cancels the heartbeat and releases the reservation even though the request is not in pending ownership

#### Scenario: Detachment leaves a live claim to its owner

- **GIVEN** terminal bookkeeping has claimed a request out of pending ownership and is still running
- **WHEN** the downstream stream detaches that request
- **THEN** detachment does not release the reservation out from under the in-flight finalization
