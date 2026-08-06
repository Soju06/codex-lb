# responses-api-compat Delta

## ADDED Requirements

### Requirement: Durable bridge ownership distinguishes process incarnations

Durable HTTP bridge ownership MUST include a per-process owner epoch in
addition to the stable bridge instance id and the existing owner fencing epoch.
The process owner epoch MUST be generated when the process starts and MUST be
persisted on newly claimed durable HTTP bridge session rows.

On startup, an instance MUST retire durable HTTP bridge sessions whose
`owner_instance_id` equals the current instance id but whose process owner epoch
is missing or differs from the current process owner epoch. Retired rows MUST
be closed and MUST NOT remain attachable through session-header,
turn-state, previous-response, latest-turn-state, or latest-response lookup.
Retired rows MUST clear stored previous-response, latest-turn-state, input
fingerprint, and pending-tool continuity anchors before any future claim can
reuse the same canonical session key.

#### Scenario: Same-container restart retires previous-process rows

- **GIVEN** a durable HTTP bridge session is ACTIVE under instance
  `container-74e8e7cda9fb` and process epoch `boot-a`
- **WHEN** codex-lb starts again in the same container id with process epoch
  `boot-b`
- **THEN** startup closes the `boot-a` durable session row
- **AND** request-target lookup for that session header, turn state, or
  previous response no longer returns the closed row
- **AND** rows already owned by `boot-b` remain attachable

### Requirement: Dead durable anchors fail with fresh-turn guidance

The proxy MUST classify proven-dead durable anchors as non-retryable recovery
boundaries.

When a continuity-bound HTTP bridge request would otherwise return a retryable
`stream_idle_timeout` or cooldown terminal, and the durable lookup that supplied
the request's previous-response anchor is proven dead because its owner
instance, process owner epoch, or lease is no longer current, the proxy MUST
return a non-retryable terminal that instructs the client to start a fresh turn.
The proxy MUST keep the existing retryable `stream_idle_timeout` semantics when
the durable owner is current and the failure is ordinary transient upstream
silence.

#### Scenario: Previous-process anchor is not retryable

- **GIVEN** a request is bound to a durable previous-response anchor
- **AND** that durable row belongs to the same instance id but a different
  process owner epoch
- **WHEN** the bridge hits the pre-submit, startup-cooldown, or retry-circuit
  idle terminal path
- **THEN** the client receives `bridge_continuity_recovery_required`
- **AND** HTTP error collection uses a non-retryable 400 response
- **AND** the response does not include `stream_idle_timeout` retry guidance

#### Scenario: Current-owner silence remains retryable

- **GIVEN** a request is bound to a durable owner whose instance id, process
  owner epoch, and lease are current
- **WHEN** upstream produces no response events through the existing idle window
- **THEN** the proxy preserves the existing retryable `stream_idle_timeout`
  behavior

### Requirement: Repeated zero-event idle failures poison dead anchors

For hard HTTP bridge keys, repeated zero-event idle failures MUST use the
existing durable retry-circuit counter to identify an anchor that should no
longer remain addressable. When consecutive failures for the same hard bridge
key reach the configured poison threshold, the proxy MUST abandon durable
continuity for that session and retire the bridge even when admission waiters
exist. The default threshold MUST be no greater than seven failures.

#### Scenario: Admission waiters cannot defer anchor poisoning forever

- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor
