# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Unavailable hard continuation owners recover via durable verified fresh replay

When a `previous_response_id` continuation resolves to a specific owner
account that is unavailable (not a lookup error, but a known owner that
cannot currently be selected), the service MUST attempt a verified-fresh-replay
recovery before failing closed, across the direct HTTP/WebSocket retry path
and the HTTP responses bridge. The verification MUST source the completed
response's input-item-count and input-fingerprint from durable storage when
the process-local continuity cache has no entry for that response id, so the
recovery is not limited to the replica or process that originally completed
the response. Sourcing from durable storage MUST NOT overwrite an existing,
more current in-memory continuity entry for the same session. Recovery MUST
still require every existing safety condition: the resend input MUST be a
self-contained fresh replay (no dangling tool-call references, no
account-scoped file references) and MUST match the recorded item count and
fingerprint exactly. Absent a verified match, the service MUST continue to
fail closed with `previous_response_owner_unavailable` exactly as before this
capability existed.

#### Scenario: cold continuity cache still recovers a resumed direct-path conversation

- **GIVEN** a `previous_response_id` continuation whose owner account is
  known from durable request-log history
- **AND** the process-local continuity cache has no entry for that response
  id (fresh process, evicted entry, or a different replica than the one that
  completed it)
- **AND** the owner account is unavailable for new selection
- **AND** the client's resend input is a self-contained fresh replay whose
  prefix exactly matches the durable item count and fingerprint
- **WHEN** the direct retry path resolves the owner and then fails to select
  it
- **THEN** the service strips the `previous_response_id` anchor and completes
  the request on a different available account
- **AND** it does not return `previous_response_owner_unavailable`

#### Scenario: HTTP bridge recovers a client-anchored continuation with an unavailable owner

- **GIVEN** a client sends a `previous_response_id` directly (not a
  proxy-injected reattach) to the HTTP responses bridge
- **AND** the resolved owner account is unavailable when the bridge attempts
  to create or reuse a session
- **AND** the client's resend input is a self-contained fresh replay whose
  prefix exactly matches the durably recorded item count and fingerprint
- **WHEN** bridge session creation fails with the owner-unavailable error
- **THEN** the bridge strips the anchor and retries the request fresh on a
  different account
- **AND** this recovery is independent of the bridge's existing
  proxy-injected-reattach recovery path

#### Scenario: unverifiable resend still fails closed

- **GIVEN** a `previous_response_id` continuation whose resolved owner is
  unavailable
- **WHEN** the resend input is not a self-contained fresh replay, or it does
  not match the recorded item count and fingerprint exactly, or no durable or
  in-memory record exists at all
- **THEN** the service returns `previous_response_owner_unavailable`
- **AND** it does not move the request to another account

#### Scenario: durable warm-up never overwrites a live continuity entry

- **GIVEN** the process-local continuity cache already holds an entry for a
  session because this process directly observed a more recent completion
- **WHEN** durable owner resolution runs for that same session
- **THEN** the existing in-memory entry is left unchanged
- **AND** the durable row is not written over it
