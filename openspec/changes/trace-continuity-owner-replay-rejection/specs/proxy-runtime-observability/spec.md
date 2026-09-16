## ADDED Requirements

### Requirement: Refused cross-account continuity replays are attributable

When a continuity owner account is unavailable and the proxy refuses to move the turn to
another account, it MUST record which proof refused it. The reason MUST come from the closed
set `file_bound`, `no_durable_lookup`, `payload_not_full_resend`, `anchor_metadata_missing`,
`prefix_fingerprint_mismatch`, `input_not_itemized`, `missing_prior_output`,
`account_scoped_input`, and MUST be exposed both as the Prometheus counter
`codex_lb_continuity_replay_rejected_total{surface, reason}` and as one bridge event log line
`owner_unavailable_replay_rejected` at WARNING carrying the reason, the hashed bridge key, the
affinity kind, the model, and the failed owner account id. Exactly one reason MUST be reported
per refusal, and it MUST be the first proof that refused in evaluation order. Recording MUST
NOT change whether the replay is attempted, MUST NOT alter any routing, selection, or failover
decision, and MUST degrade to the log line alone when the Prometheus client is absent.

#### Scenario: Owner pinned without durable anchor context

- **GIVEN** the continuity owner was resolved from the request-log index, the in-process bridge
  registry, or a durable row that carries no stored input item count
- **WHEN** that owner is unavailable and the turn fails closed
- **THEN** the counter is incremented once with `reason="no_durable_lookup"` or
  `reason="anchor_metadata_missing"` according to which condition held
- **AND** one `owner_unavailable_replay_rejected` line is logged for the same refusal

#### Scenario: Stored prefix does not match the incoming body

- **GIVEN** a durable row carries a stored input item count and full-input fingerprint
- **WHEN** the incoming body is full-resend shaped but its prefix does not match that
  fingerprint, and the unavailable owner makes the turn fail closed
- **THEN** the reason reported is `prefix_fingerprint_mismatch`, distinct from the reason
  reported when the body is not full-resend shaped (`payload_not_full_resend`) and from the
  reason reported when the row has no stored count (`anchor_metadata_missing`)

#### Scenario: History cannot leave the account

- **WHEN** the projected body still carries account-scoped state, or the projection cannot be
  built at all
- **THEN** the reason reported is `account_scoped_input`
- **WHEN** the request is pinned to an account by an uploaded input file
- **THEN** the reason reported is `file_bound`
- **WHEN** the projected body loses the prior upstream output and does not match the row's
  pending tool calls
- **THEN** the reason reported is `missing_prior_output`

#### Scenario: Replay is allowed

- **WHEN** every proof passes and the turn is re-anchored on another account
- **THEN** no reason is recorded and the counter is not incremented
- **AND** the existing `owner_unavailable_fresh_resend` event is logged as before

### Requirement: Bridge continuity-owner failures reach the request log

A pre-submit HTTP session bridge failure whose error code is `previous_response_owner_unavailable`
or `continuity_owner_conflict` MUST write exactly one `request_logs` row before the error is
returned to the client, carrying `status="error"`, that error code, the request model, and the
ingress request id also printed by the proxy's error log line, so the row and the log line are
joinable on that id. The row MUST NOT be written when the bridge instead hands the turn to the
plain HTTP upstream transport, which settles its own outcome — an error row for a turn that
then succeeded would misstate the error rate the row exists to expose. A bridge failure
carrying any other error code MUST NOT gain a request-log row from this requirement.

#### Scenario: Unroutable owner on the bridge surface

- **GIVEN** an existing bridged thread whose owner account is not selectable
- **WHEN** session creation fails with `previous_response_owner_unavailable`
- **THEN** one `request_logs` row exists for that request id with `status="error"` and
  `error_code="previous_response_owner_unavailable"`
- **AND** the client still receives the same 502 envelope as before

#### Scenario: Unrelated pre-submit failure

- **WHEN** bridge session creation fails with a capacity, transport, or cooldown error code
- **THEN** no request-log row is written by this requirement and the existing behaviour of that
  path is unchanged
