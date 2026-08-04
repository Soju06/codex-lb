## ADDED Requirements

### Requirement: Compact previous_response_id anchors are account-scoped

The HTTP-bridge session-level compact-anchor injection reduces payload size by
replacing already-stored history with `previous_response_id = session.last_completed_response_id`.
Because a `previous_response_id` can only be resumed by the account that created
it, codex-lb MUST NOT inject a compact anchor whose owning account differs from
the account that will serve the request.

codex-lb MUST record the account that owns `last_completed_response_id` whenever
that value is set — from a real upstream `response.completed` (the session's
current account) or from a durable-session restore (the durable owner account) —
and keep the two in sync.

#### Scenario: Anchor injected when the serving account owns it

- **WHEN** a Codex session follow-up turn is eligible for compact-anchor injection
- **AND** the account that owns `last_completed_response_id` equals the session's
  serving account
- **THEN** codex-lb injects `previous_response_id = last_completed_response_id`
  and trims the already-stored history prefix

#### Scenario: Anchor skipped after cross-account failover

- **WHEN** a Codex session follow-up turn is eligible for compact-anchor injection
- **AND** the account that owns `last_completed_response_id` differs from the
  session's serving account (for example the session failed over after the durable
  owner account became unavailable)
- **THEN** codex-lb MUST NOT inject the anchor
- **AND** codex-lb resends the full history to the serving account so continuity
  is preserved without an unresolvable `previous_response_id`
- **AND** the request MUST NOT stall waiting for a `response.created` that upstream
  will never send for an anchor the serving account does not own
