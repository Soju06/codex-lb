## ADDED Requirements

### Requirement: Draining owner-forward rejection can recover only before dispatch

The origin MUST treat an owner-forwarded HTTP bridge `bridge_drain_active`
failure as locally recoverable only if the target owner rejected the request
before upstream dispatch. For session-header and thread-header bootstrap
requests without `previous_response_id`, such pre-dispatch rejection MAY rebind
a turn-state anchored request locally. The origin MUST NOT use this bootstrap
rebind for previous-response continuations, for ambiguous dispatch failures, or
for owner failures whose public error code does not prove a draining-owner
rejection.

#### Scenario: Pre-dispatch drain rejection rebinds bootstrap request

- **GIVEN** a session-header or thread-header owner-forward request includes a
  turn-state anchor
- **AND** the target owner rejects it with `bridge_drain_active` before
  dispatching upstream
- **WHEN** the origin evaluates local bootstrap recovery
- **THEN** it may create or reuse a local bridge session for the request

#### Scenario: Ambiguous drain failure does not rebind

- **GIVEN** a session-header or thread-header owner-forward request includes a
  turn-state anchor
- **AND** the target owner failure is ambiguous or already acknowledged
- **WHEN** the origin evaluates local bootstrap recovery
- **THEN** it does not use the local bootstrap rebind path
