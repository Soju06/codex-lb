## ADDED Requirements

### Requirement: Compact previous_response_id anchors are account-scoped

codex-lb MUST NOT inject a compact `previous_response_id` anchor whose owning account differs from the account that will serve the request.

The HTTP-bridge compact-anchor injection reduces payload size by replacing
already-stored history with a proxy-supplied `previous_response_id`, and a
`previous_response_id` can only be resumed by the account that created it. The
rule applies to every injection site that runs after the serving account is
bound: the session-level anchor (`session.last_completed_response_id`) and the
owner-forward recovery anchor (`durable_lookup.latest_response_id`, injected
after a rebind that is allowed to land on a different account).

codex-lb MUST record the account that owns `last_completed_response_id` whenever
that value is set — from a real upstream `response.completed` (the session's
current account) or from a durable-session restore (the durable owner account) —
and keep the two in sync.

Injection sites that run before the serving account is bound stay covered by the
existing required-continuity-owner pin, which fails the request rather than
serving a proxy-injected anchor on a different account.

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

#### Scenario: Owner-forward recovery anchor skipped after a cross-account rebind

- **WHEN** an owner forward fails and the local recovery rebind binds the session
  to an account other than the durable record's owner
- **AND** the durable record still carries a `latest_response_id` the recovery
  request would otherwise anchor on
- **THEN** codex-lb MUST NOT inject that anchor
- **AND** the recovery request keeps its full input instead of a trimmed suffix

#### Scenario: Declined anchors are observable

- **WHEN** codex-lb declines a compact anchor because the serving account does not
  own it
- **THEN** codex-lb logs a `cross_account_anchor_declined` bridge event naming the
  injection site, the anchor's owning account, and the full-history-resend outcome
