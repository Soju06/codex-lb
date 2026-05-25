## ADDED Requirements

### Requirement: Retryable websocket open timeouts drain the selected account

The proxy MUST treat an upstream websocket open attempt that fails with
retryable same-contract semantics before a response stream is established as
selected-account instability for the current request attempt. It MUST record
account failure evidence and retry selection without that account while the
request budget allows. If no alternate account is available or the request
budget is exhausted, the terminal request-log row MUST preserve the websocket
failure class.

#### Scenario: HTTP bridge creation routes around websocket open timeout
- **WHEN** HTTP bridge session creation selects an account
- **AND** opening the upstream websocket fails with a retryable
  `websocket_open_timeout`
- **AND** another eligible account is available within the request budget
- **THEN** the proxy records an account failure for the first account
- **AND** retries bridge creation with the timed-out account excluded
- **AND** the client is not surfaced a terminal error for the recovered attempt

#### Scenario: HTTP bridge reconnect routes around websocket open timeout
- **WHEN** an existing HTTP bridge session reconnect selects the current account
- **AND** opening the replacement upstream websocket fails with a retryable
  `websocket_open_timeout`
- **AND** another eligible account is available within the request budget
- **THEN** the proxy records an account failure for the timed-out account
- **AND** reconnects through the alternate account

#### Scenario: Terminal open timeout keeps request-log evidence
- **WHEN** a retryable websocket open timeout cannot be recovered by alternate
  account selection before the request budget is exhausted
- **THEN** the request log records the websocket failure class as
  `upstream_websocket_open_timeout`
- **AND** the public client error remains a retryable upstream-unavailable
  response without exposing account identifiers
