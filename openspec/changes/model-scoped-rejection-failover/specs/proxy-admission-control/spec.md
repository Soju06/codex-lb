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
after content, a tool call, or terminal output evidence; when the request
has a required previous-response, turn-state, file, single-account, or
dispatched-payload owner; when the request's affinity may resolve to a hard
sticky owner the request does not carry (a durable Codex session or a raw
legacy `CODEX_SESSION` row consulted for a thread-scoped request); or when the
serving account is the single replacement selected after an account/model
rejection, whose own failure is terminal.

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

#### Scenario: Account/model replacement keeps its single-replacement budget

- **GIVEN** account A rejected the requested model and account B was selected
  as the one permitted replacement
- **AND** account C is eligible and deterministic failover is enabled
- **WHEN** B returns `response.created` followed by an output-free
  `server_is_overloaded` rejection
- **THEN** the request does not select account C
- **AND** the client observes B's `response.created` and its terminal

#### Scenario: Legacy hard owner remains fail closed

- **GIVEN** a thread-scoped HTTP Responses stream whose affinity also consults
  a raw legacy `CODEX_SESSION` row, so sticky selection may bind it to a hard
  owner the request state does not carry
- **AND** deterministic failover is enabled
- **WHEN** the selected account returns `response.created` followed by an
  output-free `server_is_overloaded` rejection
- **THEN** the request does not select another account
- **AND** the original lifecycle and terminal remain visible

#### Scenario: Public route observes only the sibling lifecycle

- **GIVEN** two eligible accounts and a fresh `POST /v1/responses` or
  `POST /backend-api/codex/responses` stream
- **WHEN** the first account returns `response.created` followed by an
  output-free overload rejection and the second completes the turn
- **THEN** the client receives exactly one `response.created`, carrying the
  second account's response id, followed by `response.completed`
- **AND** no error frame from the first account
