## ADDED Requirements

### Requirement: HTTP bridge model-transition owner conflicts use a guarded child lane

The service MUST use a guarded account-neutral child lane when a hard
continuity lookup identifies a durable owner for an incompatible model and
bridge creation returns `continuity_owner_conflict`; it may retry on that new
server-namespaced lane only when the
request is local (not forwarded), has no `previous_response_id`, has no
resolved file owner, and the effective payload passes the existing
account-neutral fresh-replay validator. The child lane MUST clear session and
turn aliases, reset the request's parent-derived affinity policy, hard
continuity anchor, and reused parent turn state, exclude the conflicting owner,
force local creation, and remain owner-bound (`hard`) once created so a later
capacity failure cannot reroute the same request to a third account. The service MUST attempt this child lane at
most once per request and MUST preserve the original conflict for every other
owner error or payload shape.

#### Scenario: Neutral model transition conflict forks locally

- **GIVEN** a durable hard continuity key belongs to account A for the old model
- **AND** the requested model is incompatible with that durable session
- **AND** the effective payload is account-neutral and has no previous-response
  or resolved file owner
- **AND** bridge creation returns `continuity_owner_conflict`
- **WHEN** the request is local to the current replica
- **THEN** the service creates one server-namespaced account-neutral child lane
- **AND** that child lane key is owner-bound (`hard`)
- **AND** it excludes account A and does not forward the request
- **AND** the child request carries no parent affinity policy, hard continuity
  anchor, or reused parent turn state
- **AND** it leaves the original hard aliases unchanged

#### Scenario: Forwarded model transition conflict remains fail-closed

- **GIVEN** the same durable model-transition conflict occurs for a forwarded
  request
- **THEN** the service returns `continuity_owner_conflict`
- **AND** it does not create an account-neutral child lane

#### Scenario: Account-bound payload remains fail-closed

- **GIVEN** the effective model-transition payload contains an account-scoped
  hosted reference such as an unpinned `input_file.file_id`
- **WHEN** bridge creation returns `continuity_owner_conflict`
- **THEN** the service returns `continuity_owner_conflict`
- **AND** it does not retry on another account

#### Scenario: Post-compaction payload without carried compact context stays fail-closed

- **GIVEN** the effective model-transition payload contains a `compaction` item
  that is not self-contained, such as a placeholder with no encrypted content or
  a compaction still in progress
- **WHEN** bridge creation returns `continuity_owner_conflict`
- **THEN** the service returns `continuity_owner_conflict`
- **AND** it does not fork the request onto an account that never held the
  compacted context

#### Scenario: Child lane conflict is not forked again

- **GIVEN** the guarded child lane was already created for this request
- **WHEN** creation on that lane also returns `continuity_owner_conflict`
- **THEN** the service returns that error to the caller
- **AND** it does not create a further account-neutral lane
