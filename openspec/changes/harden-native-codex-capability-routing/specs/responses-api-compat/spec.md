## ADDED Requirements

### Requirement: Direct WebSocket turns revalidate account capabilities

Before forwarding each unsent direct-WebSocket `response.create` frame on an
already-open upstream socket, the service MUST revalidate that the selected
account remains active, remains in the API-key scope, supports the requested
model and service tier, and still satisfies any preferred-owner constraint.

#### Scenario: Account is paused after the socket opens

- **GIVEN** a direct upstream WebSocket was opened on an active account
- **AND** that account is paused before the next fresh turn
- **WHEN** the next `response.create` is ready to send
- **THEN** the service does not send that frame on the paused account
- **AND** it reconnects to another eligible account when the request is safe to
  start fresh

#### Scenario: Short previous-response continuation remains owner-bound

- **GIVEN** a turn contains a client-owned `previous_response_id`
- **AND** its input contains only a delta that depends on the stored response
- **AND** its owner account is unavailable or no longer supports the request
- **WHEN** the turn is ready to send
- **THEN** the service fails with a retryable continuity error
- **AND** it does not move the hard chain to another account

#### Scenario: Verified client full resend survives owner quota exhaustion

- **GIVEN** a turn contains a client-owned `previous_response_id`
- **AND** its input is a verified self-contained full resend, including every
  tool call required by any included tool output
- **AND** the owner account reports a retryable quota failure before visible
  output
- **WHEN** another model- and tier-eligible account is available
- **THEN** the service removes the owner-scoped `previous_response_id`
- **AND** it replays the full request on the other eligible account
- **AND** it does not expose the first account's quota error downstream

#### Scenario: HTTP image bypass uses the same safe replay boundary

- **GIVEN** an HTTP Responses request bypasses the WebSocket bridge because it
  contains an input image
- **AND** it carries a verified self-contained full resend
- **WHEN** its previous-response owner reports a retryable quota failure before
  visible output
- **THEN** the HTTP streaming retry path may remove the anchor and select
  another eligible account
- **AND** a successful response is associated with the account that created it
  for subsequent owner lookup

#### Scenario: Proxy-injected continuity has a safe fresh form

- **GIVEN** the proxy injected a previous-response anchor for optimization
- **AND** it retained a semantically equivalent fresh request body
- **WHEN** the current account becomes ineligible before send
- **THEN** the service may retire the drained socket and reconnect using the
  fresh request body without the injected anchor

### Requirement: Cross-transport failover preserves semantic continuity

The service MUST apply one replay-safety rule across direct WebSocket, HTTP
bridge, and HTTP streaming transports. It MUST NOT treat a transport change as
permission to reroute an owner-bound delta, and it MAY reroute only when a
fresh request body is independently sufficient to preserve conversation and
tool semantics.

#### Scenario: Tool output without its call cannot move accounts

- **GIVEN** a continuation contains a tool output whose corresponding tool call
  exists only in an owner-scoped previous response
- **WHEN** the owner is unavailable
- **THEN** the service fails closed instead of replaying the orphaned tool
  output on another account

#### Scenario: Self-contained tool history may move accounts

- **GIVEN** a full resend includes a tool call before every corresponding tool
  output
- **AND** no account-scoped file reference requires the old owner
- **WHEN** the old owner is unavailable before visible output
- **THEN** the service may replay that history as a fresh request on another
  eligible account

### Requirement: Selected Codex installation identity is internally consistent

For native Codex requests, when an account-specific installation id is applied,
the service MUST use that same id in `x-codex-installation-id` and in an
existing `x-codex-turn-metadata.installation_id`. Missing or malformed turn
metadata MUST be preserved rather than invented or discarded.

#### Scenario: Both canonical metadata carriers are present

- **WHEN** a native request contains both installation metadata carriers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id
