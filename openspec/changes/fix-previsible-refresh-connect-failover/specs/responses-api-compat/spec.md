## ADDED Requirements

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and
upstream-connect failure handling. If the pinned account cannot refresh or open
the upstream compact connection before any compact response is emitted, the proxy
MUST surface a stable upstream-unavailable failure for that request instead of
excluding the pinned account and replaying the compact request on another
account. This fail-closed rule applies only to file-pinned compact requests;
replayable compact/connect requests without a live file-id pin continue to use
the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure

- **GIVEN** `file_pinned` was uploaded through `account_a` and its in-memory pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the
  failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try
  another eligible account

#### Scenario: retained file-backed bridge replay remains owner-bound

- **GIVEN** an HTTP bridge precreated request uses a proxy-injected
  `previous_response_id` anchor
- **AND** the retained retry-safe full body references an account-scoped
  uploaded file through `input_file.file_id` or file-backed `input_image`
- **WHEN** the bridge retries after an upstream close before visible output
- **THEN** the proxy keeps the anchored request owner-bound instead of stripping
  the anchor, excluding the owner, and replaying the file reference on another
  account
- **AND** if the file owner cannot be reselected, the retry fails closed instead
  of reconnecting the bridge on a replacement account

#### Scenario: verified owner refresh failover releases the failed stream lease

- **GIVEN** a streaming request selects the previous-response owner and holds an
  account stream lease
- **AND** a locally verified full resend permits failover after that owner fails
  refresh or connect before output is emitted
- **WHEN** the proxy excludes the failed owner and selects a replacement account
- **THEN** the failed owner's stream lease is released before replacement
  selection so the owner does not retain stale local pressure

### Requirement: Stale HTTP bridge previous-response aliases fail closed

The HTTP bridge MUST NOT treat a stale previous-response alias as a model
transition unless the indexed session's model is incompatible with the incoming
request. When a previous-response alias resolves to a closed or inactive session
for the same model and no durable recovery owner is available, the proxy MUST
surface the existing continuity-lost failure instead of creating or selecting a
replacement bridge.

#### Scenario: stale same-model previous-response alias fails closed

- **GIVEN** the previous-response index still points to an inactive HTTP bridge
  session for the same model
- **AND** no durable owner lookup is available for that response id
- **WHEN** a request arrives with that `previous_response_id`
- **THEN** the proxy fails closed with the stream-incomplete continuity error
- **AND** it does not create a replacement bridge for the stale response id

### Requirement: Cross-account bridge retries clear turn-state

When a pre-visible HTTP bridge request is proven safe to replay on another account, the proxy MUST clear the retired account's upstream and downstream turn-state before opening the replacement connection. The replacement handshake MUST NOT carry an `x-codex-turn-state` header learned from the excluded account.

#### Scenario: safe bridge replay excludes the stalled account

- **GIVEN** a pre-visible HTTP bridge request is proven safe to replay
- **WHEN** the failed bridge account is excluded before reconnect
- **THEN** the proxy clears the retired account's turn-state fields and header
- **AND** the replacement account receives no turn-state from the retired socket
