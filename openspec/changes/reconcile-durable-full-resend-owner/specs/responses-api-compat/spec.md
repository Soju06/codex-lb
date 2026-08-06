## MODIFIED Requirements

### Requirement: HTTP bridge MUST support fresh upstream reattach from durable continuity

The service MUST retain the durable owner account for routing when a bridged
HTTP request arrives for a valid hard continuity key but no live local session
or active remote owner remains. If the client supplies a full resend whose
stored prefix matches the durable input count and fingerprint and whose suffix
either proves that prior assistant output is retained before the new input or
exactly settles the response-bound pending-tool-call manifest, the service MUST
submit that complete payload on the fresh upstream bridge without injecting the
durable `previous_response_id`. Otherwise, when the request needs the durable
response anchor to represent prior context, the service MUST preserve existing
durable-anchor reattach behavior. The service MUST NOT replay a request after
uncertain upstream acceptance.

Complete full-resend eligibility MUST be represented by an immutable
request-local proof that binds the current payload fingerprint to the durable
session ID, owner account, latest response ID, stored count, stored fingerprint,
and pending-tool-call manifest identity. The proof MUST be created only by the
count, fingerprint, and retained-output or response-bound pending-tool-call
verifier; it MUST NOT be accepted from a caller, persisted, deserialized, or
ordinarily constructed, and mutation or durable-state substitution MUST
invalidate it.

When that proof authorizes a fresh owner-bound bridge and the incoming affinity
comes from a broad session header, the service MUST omit the downstream
session/turn aliases from the fresh upstream connection and MUST NOT consult
the broad legacy sticky row during account selection. It MUST retain the
durable canonical bridge key, require the durable owner account, preserve Codex
session behavior for subsequent turns, and leave the broad sticky row
unchanged. Conflicting specific turn-state, previous-response, bridge, or file
owners MUST still fail closed. This broad-alias reconciliation MUST NOT itself
rebind the request to another account; existing account-neutral full-resend
recovery after a genuine owner-unavailable result remains governed by its
separate replay-safety requirements.

#### Scenario: stale broad session owner does not loop a verified full resend

- **GIVEN** a hard durable session is owned by account B and records a latest
  response ID, positive input count, and full fingerprint
- **AND** no live local bridge or active remote owner remains
- **AND** a broad legacy session-header sticky row points at account A
- **WHEN** the client sends a full resend whose stored prefix matches both
  durable values and whose suffix retains completed prior output before fresh
  input or exactly settles the response-bound pending-tool-call manifest
- **THEN** the service opens the fresh bridge on account B
- **AND** it submits the complete payload without `previous_response_id`
- **AND** it does not consult or rewrite the broad legacy row
- **AND** it omits downstream session and turn aliases from the fresh upstream
  connection

#### Scenario: recovered bridge keeps incremental continuity

- **GIVEN** a verified full resend established a fresh owner-bound bridge
- **WHEN** that bridge completes and a later incremental turn arrives
- **THEN** the later turn remains on the same account
- **AND** the bridge may use the newly established response anchor

#### Scenario: incomplete resend cannot bypass a broad owner conflict

- **GIVEN** a durable owner conflicts with a broad legacy session owner
- **WHEN** the input prefix does not match, retained prior output is absent, the
  payload or durable identity changes after verification, or the request is
  incremental
- **THEN** no full-resend proof authorizes reconciliation
- **AND** the request retains existing anchor and fail-closed owner behavior

#### Scenario: specific owner conflict remains fail-closed

- **GIVEN** a verified full resend also contains a turn-state,
  previous-response, bridge, or file owner that conflicts with the durable
  owner
- **WHEN** continuity is resolved
- **THEN** the request fails with `continuity_owner_conflict`
- **AND** the broad-session reconciliation does not choose either account
