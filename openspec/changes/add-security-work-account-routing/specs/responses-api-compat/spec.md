## ADDED Requirements

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

The classifier MUST recognize both the legacy cybersecurity-risk message and the current `This content can't be shown` / `We take extra caution with cybersecurity requests` Trusted Access denial. For an eligible HTTP-bridge or websocket request, the retry MUST reconnect the existing downstream session to the authorized account and continue without forwarding the denial as the terminal response.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

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
