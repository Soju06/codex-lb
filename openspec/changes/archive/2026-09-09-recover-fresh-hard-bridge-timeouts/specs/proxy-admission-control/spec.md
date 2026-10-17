# proxy-admission-control Delta

## ADDED Requirements

### Requirement: Fresh hard bridge requests may recover across accounts

When a hard HTTP bridge request is still pre-response and has no
`previous_response_id`, hard continuity anchor, proxy-injected anchor, or
account-scoped file ownership, pre-response recovery MAY exclude the failed
session account and select another eligible account. The request MUST retain
its original request body and deadline. Requests carrying any of those
continuity or ownership markers MUST remain pinned to the required account.

#### Scenario: Fresh hard request switches after silent upstream failure

- **GIVEN** a hard session-header request has sent `response.create`
- **AND** upstream has not emitted `response.created` or any response event
- **AND** the request has no previous-response, turn-state, proxy-injected
  anchor, or account-scoped file ownership
- **WHEN** pre-response recovery retries the request
- **THEN** the failed account is excluded from selection
- **AND** another eligible account may receive the unchanged request body
- **AND** the original request deadline remains in force

#### Scenario: Eventless watchdog gives fresh requests one bounded recovery

- **GIVEN** a hard session-header request has reached the eventless
  `response.created` watchdog without response events
- **AND** the request has no previous-response, turn-state, proxy-injected
  anchor, or account-scoped file ownership
- **WHEN** the client-safe watchdog deadline expires
- **THEN** the proxy attempts the same bounded pre-response recovery once
- **AND** the failed account is excluded when recovery selects a replacement
- **AND** if recovery is unavailable, the proxy preserves the existing
  terminal timeout behavior

#### Scenario: Fresh account recovery bypasses a stale retry circuit

- **GIVEN** a hard session key has an active retry cooldown from repeated
  pre-response failures
- **AND** the pending request is fresh, self-contained, and has no continuity
  or account-ownership marker
- **WHEN** bounded pre-response recovery is attempted
- **THEN** the request may bypass that cooldown once to exclude the failed
  account
- **AND** continuity-bound requests remain subject to the retry cooldown

#### Scenario: Continuity-bound hard request remains pinned

- **GIVEN** a hard request has a previous-response id, continuity anchor,
  proxy-injected anchor, or account-scoped file ownership
- **WHEN** pre-response recovery retries the request
- **THEN** the original account remains required
- **AND** the request is not replayed through another account

#### Scenario: Proof-gated client full resend replays on the continuity owner

- **GIVEN** a hard request has a previous-response id and a client-provided
  full resend whose input body has passed the bridge's retry-safety checks
- **AND** upstream has not emitted `response.created` or any response event
- **WHEN** bounded pre-response recovery is attempted
- **THEN** the bridge may strip the previous-response id and replay the verified
  full body once
- **AND** recovery remains pinned to the original continuity owner
- **AND** an unverified continuation remains fail-closed

#### Scenario: Unsafe continuity timeout does not wait through an unusable cooldown

- **GIVEN** a hard continuation has no proof-gated full resend available
- **AND** the retry circuit is cooling down after repeated pre-response failures
- **WHEN** the downstream keepalive window expires
- **THEN** the proxy fails the stream closed immediately
- **AND** it does not hold the client connection open until the cooldown ends
- **AND** the client may retry with its continuity payload intact
