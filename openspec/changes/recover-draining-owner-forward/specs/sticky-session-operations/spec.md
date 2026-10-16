## ADDED Requirements

### Requirement: Draining owner-forward rejection can recover only before dispatch

The origin MUST treat an owner-forwarded HTTP bridge `bridge_drain_active`
failure as locally recoverable only if the target owner rejected the request
before upstream dispatch. For bootstrap requests without
`previous_response_id`, such pre-dispatch rejection MAY rebind the request
locally when its bridge key is a session-header or thread-header key, or a
turn-state-header key whose request also carries a session or thread header
that a local creator can fall back to. A turn-state-header key without such a
fallback MUST keep the owner's original retryable `bridge_drain_active`
envelope, whatever the turn-state text looks like; generated-versus-explicit
classification comes from recorded provenance, not from the value's prefix.
The origin MUST NOT use this bootstrap rebind for previous-response
continuations, for ambiguous dispatch failures, or for owner failures whose
public error code does not prove a draining-owner rejection. An explicit
previous-response continuation rejected with `bridge_drain_active` MUST
preserve the owner's original error envelope and MUST NOT enter generic
previous-response recovery. Before a locally recovered request is submitted,
the origin MUST reuse or settle its original API-key reservation; it MUST NOT
hold a second reservation for the same request.

#### Scenario: Pre-dispatch drain rejection rebinds bootstrap request

- **GIVEN** an owner-forward request whose bridge key is a session-header or
  thread-header key, or a turn-state-header key with a session or thread header
  fallback
- **AND** the target owner rejects it with `bridge_drain_active` before
  dispatching upstream
- **WHEN** the origin evaluates local bootstrap recovery
- **THEN** it may create or reuse a local bridge session for the request

#### Scenario: Turn-state-only request keeps the retryable drain rejection

- **GIVEN** an owner-forward request whose only bridge identity is a turn-state
  header, whether origin-minted, WebSocket-minted or client-chosen
- **AND** the target owner rejects it with `bridge_drain_active` before
  dispatching upstream
- **WHEN** the origin evaluates local bootstrap recovery
- **THEN** it returns the owner's original `bridge_drain_active` envelope
  instead of a local continuity error

#### Scenario: Ambiguous drain failure does not rebind

- **GIVEN** an owner-forward request eligible for the bootstrap rebind above
- **AND** the target owner failure is ambiguous or already acknowledged
- **WHEN** the origin evaluates local bootstrap recovery
- **THEN** it does not use the local bootstrap rebind path
