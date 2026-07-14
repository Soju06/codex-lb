# upstream-proxy-routing Delta Specification

## ADDED Requirements

### Requirement: Confirmed account-proxy connection failures fail over safely

When an account-routed transport reports that it could not connect to the
selected proxy endpoint and proves that the upstream request was not
dispatched, the service MUST classify the failure with sanitized structured
pre-dispatch provenance. For a route with another usable endpoint in the same
proxy pool, the client MUST try that endpoint before moving accounts, including
for a non-idempotent request. If the pool cannot connect, movable Responses
requests MUST exclude the failed account and retry another eligible account
within the existing request budget and attempt limits.

This behavior MUST cover raw HTTP/SSE, native Responses WebSocket, and the HTTP
responses bridge. Before recording transient account backoff, the service MUST
release response-create and stream leases held for the failed account. A
request-scoped API-key reservation MUST remain singular across an internal
pre-dispatch failover and MUST settle or release exactly once at the terminal
request outcome. The confirmed failure MUST place the account at the existing
bounded transient error-backoff floor, but MUST NOT pause, deactivate,
rate-limit, or quota-penalize it.

The service MUST NOT replay a request when dispatch is unknown or when the
request depends on hard previous-response, turn-state, uploaded-file,
single-account, or other required account ownership. If no eligible
replacement account exists, the service MUST preserve the original sanitized
upstream-unavailable failure instead of replacing it with a generated
`no_accounts` error.

#### Scenario: POST uses a healthy endpoint from the same proxy pool

- **GIVEN** a non-idempotent Responses POST is routed through a proxy pool with two endpoints
- **AND** connecting to the first endpoint fails before request dispatch
- **WHEN** the second endpoint is reachable
- **THEN** the service sends the request through the second endpoint
- **AND** it does not move the request to another account

#### Scenario: movable request retries another account

- **GIVEN** two eligible accounts and the first account's complete proxy route refuses connections before dispatch
- **WHEN** a fresh Responses request has no hard account ownership
- **THEN** the service releases the first account's response-create and stream leases
- **AND** it records bounded transient backoff for the first account
- **AND** it excludes the first account and completes through the second account
- **AND** no failure event from the first attempt is forwarded downstream

#### Scenario: hard account ownership fails closed

- **GIVEN** a Responses request depends on a previous-response owner or an account-scoped uploaded file
- **AND** the required account's proxy refuses the connection before dispatch
- **WHEN** another account is otherwise eligible
- **THEN** the service does not send the request to the other account
- **AND** it returns the sanitized upstream-unavailable failure for the required account

#### Scenario: ambiguous transport failure is not replayed

- **WHEN** a POST transport failure cannot prove that request dispatch was impossible
- **THEN** the service does not use that failure as authorization to retry another proxy endpoint or account

#### Scenario: empty replacement pool preserves the original failure

- **GIVEN** a movable request has a confirmed pre-dispatch proxy connection failure
- **AND** no other eligible account can be selected
- **THEN** the client receives the original sanitized upstream-unavailable failure
- **AND** the failure is not replaced with `no_accounts`
