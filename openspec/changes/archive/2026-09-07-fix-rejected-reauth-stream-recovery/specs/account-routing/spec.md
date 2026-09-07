## ADDED Requirements

### Requirement: Proven rejected reauthentication credentials stop routing

When an HTTP Responses access token is rejected with 401 and forced refresh fails permanently or the refreshed access token is again rejected with 401, the proxy MUST persist `reauth_required` with the existing `account_auth_invalidated` reason for that credential generation. Ordinary selection and live bridge reuse MUST reject that state even when JWT expiry is unknown or in the future. This requirement overrides warning-only routing only for this proven access-authentication failure; a permanent refresh failure without an upstream access-token rejection MUST retain the existing warning-only behavior.

The status update MUST be conditioned on the rejected access-token and refresh-token ciphertexts and current status fields. A concurrent credential repair MUST NOT be overwritten or marked unavailable by the stale rejection. A later refresh-only failure MUST NOT weaken the persisted access-rejection reason. Reauthentication or reimport that repairs credentials and clears the reason MUST restore eligibility. Routing-unavailable evidence MUST survive a cache refresh and be observable by another replica through the routing availability snapshot.

#### Scenario: New messages avoid the rejected warning account

- **GIVEN** account A has unknown or future access-token expiry and account B is healthy
- **WHEN** A's upstream 401 is followed by permanent forced-refresh failure
- **THEN** a later independent message selects B without sending to A
- **AND** A remains visible as requiring reauthentication

#### Scenario: A refresh warning alone does not retire usable access

- **GIVEN** A requires reauthentication because its refresh token is invalid
- **WHEN** its stored access token has not been rejected and is not known expired
- **THEN** ordinary routing can still select A

#### Scenario: Concurrent access-only repair survives stale rejection

- **GIVEN** a request used A's old access token
- **WHEN** another actor replaces that access token before the rejection is persisted
- **THEN** the guarded rejection update does not modify the repaired row or publish an unavailable mark

#### Scenario: Repair clears the routing block

- **GIVEN** A was excluded for proven access-authentication failure
- **WHEN** repaired credentials are imported and routing snapshots refresh
- **THEN** A can be selected and reused again
