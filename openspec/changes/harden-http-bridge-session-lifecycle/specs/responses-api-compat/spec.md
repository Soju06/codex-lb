## ADDED Requirements

### Requirement: HTTP bridge stale-session cleanup is bounded

The HTTP responses bridge MUST NOT hold the global bridge session registry lock
while awaiting operations that can block on a stale session's upstream websocket,
per-session pending lock, durable session repository, account lease release, or
other external cleanup work.

When stale bridge sessions are discovered during `/v1/responses`,
`/backend-api/codex/responses`, `/v1/responses/compact`, or
`/backend-api/codex/responses/compact` startup, the registry lock MAY be used to
remove closed or idle sessions from in-memory indexes, but potentially blocking
session close/fail-pending work MUST run after the lock is released or under a
bounded cleanup path. A wedged stale session MUST NOT prevent unrelated soft
HTTP Responses work from creating or reusing another bridge session.

If cleanup cannot complete within the bounded cleanup path, the service MUST log
a low-cardinality local bridge cleanup warning and continue protecting registry
progress. Requests that cannot safely proceed because a hard-continuity session
is unavailable MUST fail closed with an explicit local overload or continuity
error rather than silently hanging.

#### Scenario: wedged stale pending lock does not block fresh soft request

- **GIVEN** the HTTP responses bridge has an idle or stale local session whose
  pending-request lock does not complete promptly
- **WHEN** a new soft-affinity `/v1/responses` request starts bridge session
  selection
- **THEN** the global bridge registry lock is not held indefinitely by stale
  cleanup
- **AND** the new request either creates/reuses an eligible bridge session or
  returns an explicit bounded local error
- **AND** it does not hang before account selection or bridge create/reuse
  logging

#### Scenario: stale close runs outside registry lock

- **GIVEN** bridge startup identifies an idle stale session that must be closed
- **WHEN** closing that session awaits upstream-reader cancellation, websocket
  close, durable release, or account lease release
- **THEN** the global bridge registry lock is already released
- **AND** unrelated bridge startup requests can continue to inspect or mutate
  the registry
