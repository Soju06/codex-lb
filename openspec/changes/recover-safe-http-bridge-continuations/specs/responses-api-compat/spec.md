# responses-api-compat Delta

## ADDED Requirements

### Requirement: Proof-gated recovery attempts are durably fenced

When an HTTP bridge request has a verified, account-neutral, unanchored full
resend body, the proxy MUST record that request fingerprint in the durable
recovery journal before dispatching it upstream. The record MUST be owned by
the current durable session owner epoch and MUST start in `unknown` state.
Requests without that replay-safety proof MUST NOT create a recovery-journal
record.

#### Scenario: Safe resend is journaled before dispatch

- **GIVEN** a request has a verified full-resend body that is safe to replay
  without `previous_response_id`
- **WHEN** the proxy admits the request for upstream dispatch
- **THEN** the durable journal contains one `unknown` record for its session
  and request fingerprint before `response.create` is sent

#### Scenario: Suppressed request is not journaled

- **GIVEN** a hard session retry circuit is cooling down
- **WHEN** the request is rejected before upstream dispatch
- **THEN** no recovery-journal record is created or refreshed

### Requirement: Durable replay is limited to ambiguous transport outcomes

The proxy MUST consume an `unknown` recovery-journal record for a fresh
account-neutral replay only after an ambiguous transport outcome, represented
by `stream_incomplete`, `stream_idle_timeout`, or
`upstream_request_timeout`, and only before any response event or downstream
output. Explicit deterministic `response.failed` errors MUST settle normally
and MUST NOT trigger a cross-account replay or consume the recovery fence.

#### Scenario: Transport ambiguity permits one replay

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** the upstream closes or times out before any response event
- **WHEN** the bridge handles the ambiguous transport failure
- **THEN** the record is atomically claimed and the request is replayed once
  on a fresh account-neutral upstream session

#### Scenario: Deterministic failure is not replayed

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** upstream emits an explicit pre-output `response.failed` such as an
  invalid request or quota rejection
- **WHEN** the bridge handles that terminal event
- **THEN** it forwards the terminal failure
- **AND** it leaves the journal available for settlement without replaying on
  another account

### Requirement: Recovery journal settlement is owner-fenced and idempotent

After a replayed request reaches `response.completed`, the proxy MUST mark its
journal record `replayed` only through the current durable owner epoch and
MUST retain the downstream response id when available. Repeated settlement,
stale owners, and concurrent claim attempts MUST NOT produce a second replay.
The migration MUST be on the current Alembic head and startup schema checks
MUST require the journal table.

#### Scenario: Completed replay settles once

- **GIVEN** a replayed request completes successfully
- **WHEN** the completion event is processed
- **THEN** the matching journal record becomes `replayed`
- **AND** a later retry cannot claim it again

#### Scenario: Stale owner cannot settle or replay

- **GIVEN** a journal record belongs to a newer durable owner epoch
- **WHEN** an old replica attempts settlement or replay
- **THEN** the operation is rejected without changing the record state
