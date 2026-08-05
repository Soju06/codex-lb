# upstream-proxy-routing Delta Specification

## ADDED Requirements

### Requirement: Confirmed account-proxy connection failures fail over safely

When an account-routed transport reports that it could not connect to the selected proxy endpoint and proves that the upstream request was not dispatched, the service MUST classify the failure with sanitized structured pre-dispatch provenance. For a route with another usable endpoint in the same proxy pool, the client MUST try that endpoint before moving accounts, including for a non-idempotent request. If the pool cannot connect, movable Responses requests MUST exclude the failed account and retry another eligible account within the existing request budget and attempt limits.

This behavior MUST cover raw HTTP/SSE, native Responses WebSocket, and the HTTP responses bridge. Before recording transient account backoff, the service MUST release response-create and stream leases held for the failed account. A request-scoped API-key reservation MUST remain singular across an internal pre-dispatch failover, MUST settle or release at the terminal request outcome before the account-health write, and MUST NOT be reacquired solely for the internal failover. If neither settlement nor fallback release can be confirmed, the service MUST leave the health write unapplied. HTTP-bridge startup cleanup MUST release only an unowned current request lifecycle, and each reservation lifecycle MUST drain only its own health writes after confirmed settlement or release. The confirmed failure MUST place the account at the existing bounded transient error-backoff floor, but MUST NOT pause, deactivate, rate-limit, or quota-penalize it.

For a raw HTTP/SSE Responses stream only, when local continuity evidence verifies that a previous-response continuation contains a complete fresh replay which can be resent without its owner anchor, a confirmed pre-dispatch failure on that owner MUST remove the anchor and retry another eligible account. This exception MUST NOT apply when the request also depends on turn-state, uploaded-file, single-account, or other required account ownership, or after any output becomes downstream-visible. Native Responses WebSocket and HTTP responses bridge startup MUST NOT use this raw HTTP/SSE exception to remove a required previous-response owner anchor; they retain their separately specified transport-specific replay gates.

The service MUST NOT replay a request when dispatch is unknown or when the request depends on a previous-response owner not covered by the raw HTTP/SSE exception above or another separately specified transport-specific recovery rule, turn-state, uploaded-file, single-account, or other required account ownership. If no eligible replacement account exists, the service MUST preserve the original sanitized upstream-unavailable failure instead of replacing it with a generated `no_accounts` error.

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
- **AND** it settles or releases any request-scoped API-key reservation before the account-health write
- **AND** it records bounded transient backoff for the first account
- **AND** it excludes the first account and completes through the second account
- **AND** no failure event from the first attempt is forwarded downstream

#### Scenario: hard account ownership fails closed

- **GIVEN** a Responses request depends on a previous-response owner or an account-scoped uploaded file
- **AND** the required account's proxy refuses the connection before dispatch
- **WHEN** another account is otherwise eligible
- **THEN** the service does not send the request to the other account
- **AND** it returns the sanitized upstream-unavailable failure for the required account

#### Scenario: raw HTTP/SSE verified full replay moves off a dead previous-response owner

- **GIVEN** a raw HTTP/SSE Responses stream with a previous-response continuation whose complete fresh input has been verified locally
- **AND** the request has no turn-state, uploaded-file, single-account, or other required ownership
- **WHEN** the previous-response owner's proxy refuses the connection before dispatch
- **THEN** the service removes the previous-response owner anchor
- **AND** it excludes the failed owner and completes through another eligible account
- **AND** no failure event from the failed owner is forwarded downstream

#### Scenario: ambiguous transport failure is not replayed

- **WHEN** a POST transport failure cannot prove that request dispatch was impossible
- **THEN** the service does not use that failure as authorization to retry another proxy endpoint or account

#### Scenario: empty replacement pool preserves the original failure

- **GIVEN** a movable request has a confirmed pre-dispatch proxy connection failure
- **AND** no other eligible account can be selected
- **THEN** the client receives the original sanitized upstream-unavailable failure
- **AND** the failure is not replaced with `no_accounts`
