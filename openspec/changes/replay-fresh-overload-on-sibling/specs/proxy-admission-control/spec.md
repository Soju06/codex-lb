## ADDED Requirements

### Requirement: Fresh output-free overloads probe an alternate account

Codex-lb MUST make a bounded alternate-account selection after a fresh,
account-neutral Responses stream with no model output returns
`server_is_overloaded` or `overloaded_error` while
deterministic failover is enabled. The terminal MAY be the first SSE event or
follow only `response.created` and optional `response.in_progress`. The
rejected account MUST be excluded from that request's replay selection and its
existing health and overload-backoff handling MUST still run. The client MUST
observe only the replacement account's response lifecycle.

The recovery MUST NOT cross accounts when deterministic failover is disabled;
after content, a tool call, or terminal output evidence; or when the request
has a required previous-response, turn-state, durable Codex-session, file,
single-account, or dispatched-payload owner.

#### Scenario: Fresh HTTP first turn moves off an overloaded account

- **GIVEN** a self-contained HTTP Responses stream selects account A
- **AND** account B is eligible for the requested model
- **AND** deterministic failover is enabled
- **WHEN** A returns `response.created` followed by an output-free
  `server_is_overloaded` or `overloaded_error` rejection
- **THEN** the stream excludes A and sends one bounded replay selection to B
- **AND** the client observes only B's response lifecycle

#### Scenario: First-event overload moves off an overloaded account

- **GIVEN** a self-contained HTTP Responses stream selects account A
- **AND** account B is eligible for the requested model
- **AND** deterministic failover is enabled
- **WHEN** A returns `server_is_overloaded` or `overloaded_error` as its first
  SSE event
- **THEN** the stream excludes A and sends one bounded replay selection to B
- **AND** the client observes only B's response lifecycle

#### Scenario: Continuity owner remains fail closed

- **GIVEN** a Responses stream requires account A through a previous-response
  owner
- **AND** deterministic failover is enabled
- **WHEN** A returns `server_is_overloaded`
- **THEN** the request does not select account B
- **AND** it retains the existing terminal continuity behavior

#### Scenario: Visible output remains fail closed

- **GIVEN** a self-contained HTTP Responses stream selects account A
- **AND** deterministic failover is enabled
- **WHEN** A emits content or a tool-call item before `server_is_overloaded`
- **THEN** the request does not select account B
- **AND** the original response lifecycle and terminal remain visible

#### Scenario: Disabled deterministic failover remains fail closed

- **GIVEN** a self-contained HTTP Responses stream selects account A
- **AND** deterministic failover is disabled
- **WHEN** A returns an output-free `server_is_overloaded` rejection
- **THEN** the request does not select account B
- **AND** the terminal remains visible
