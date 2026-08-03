## ADDED Requirements

### Requirement: Verified OAuth Live callers resolve to one imported seat

The system SHALL validate a supplied ChatGPT bearer and `chatgpt-account-id` pair against the upstream usage endpoint before treating token claims as caller identity. After validation, it MUST resolve exactly one eligible imported Account using stable per-seat identity plus ChatGPT account/workspace context. A legacy credential without stable seat identity MUST resolve only when its verified account/workspace candidate set contains exactly one eligible imported Account.

#### Scenario: Shared workspace seats remain isolated

- **GIVEN** two imported Accounts share a ChatGPT workspace account id and have distinct `chatgpt_user_id` values
- **WHEN** one seat presents verified OAuth credentials to a Live Voice route
- **THEN** the caller resolves to the Account with the matching seat id
- **AND** the other seat's policy is not consulted

#### Scenario: Missing or ambiguous imported seat fails closed

- **WHEN** verified OAuth credentials map to no imported Account or more than one eligible legacy candidate
- **THEN** caller resolution fails before policy lookup and upstream account selection
- **AND** the response contains no token, seat id, account id, workspace id, or candidate details

### Requirement: OAuth identity validation is bounded and coalesced

OAuth identity cache and singleflight keys MUST be a one-way digest over both bearer and normalized `chatgpt-account-id`. Concurrent cache misses for the same pair MUST share one upstream validation. Positive entries MUST expire within 60 seconds and no later than token expiry; credential-denial entries MUST expire within 5 seconds. Upstream availability and rate-limit failures MUST preserve typed failure semantics and MUST NOT become successful cached identity.

#### Scenario: Concurrent handshakes share validation

- **WHEN** call-create and sideband concurrently validate the same uncached bearer/account pair
- **THEN** one upstream usage validation runs
- **AND** every waiter receives the same typed identity or credential denial

