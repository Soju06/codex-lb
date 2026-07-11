## ADDED Requirements

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

The classifier MUST recognize both the legacy cybersecurity-risk message and the current `This content can't be shown` / `We take extra caution with cybersecurity requests` Trusted Access denial. For an eligible HTTP-bridge or websocket request, the retry MUST reconnect the existing downstream session to the authorized account and continue without forwarding the denial as the terminal response.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: Classified Codex lineage has no authorized account

- **WHEN** a root Codex session or any of its child turns has been classified as requiring security-work authorization
- **AND** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb returns the original security-work authorization error without selecting an ordinary account

#### Scenario: Classified Codex lineage remains classified after routing cleanup

- **WHEN** a root Codex session has been classified as requiring security-work authorization
- **AND** its ordinary account-affinity row is removed because no authorized account can currently be selected
- **THEN** codex-lb MUST retain a separate durable security-work marker for that lineage
- **AND** later turns and child turns MUST remain restricted to security-work-authorized accounts

#### Scenario: Failed authorized reconnect preserves classification

- **WHEN** codex-lb has durably classified a session as requiring security-work authorization
- **AND** reconnecting that session to an authorized account fails
- **THEN** codex-lb MUST preserve the durable security-work requirement
- **AND** a later retry MUST NOT return the session to the ordinary account pool

#### Scenario: Unrooted request has no authorized account

- **WHEN** an unrooted request attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb MAY continue normal account failover when safe

#### Scenario: Pinned requests move only with a self-contained fresh replay

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **AND** the request does not carry a validated self-contained full resend without account-scoped files
- **THEN** codex-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure
- **BUT WHEN** a previous-response owner is unavailable and the WebSocket request carries a validated self-contained fresh replay
- **THEN** codex-lb drops the unusable anchor and retries that fresh body on a security-work-authorized account

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** codex-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt

#### Scenario: Non-replayable WebSocket denial retires the ordinary connection

- **WHEN** a direct WebSocket request receives a security-work authorization denial after replay is no longer safe
- **THEN** codex-lb forwards the terminal denial for that request
- **AND** codex-lb MUST retire the ordinary upstream connection before accepting another turn for the classified lineage
- **AND** the next turn MUST pass through security-work-authorized account selection
