## MODIFIED Requirements

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key`, `401 token_invalidated`, or
`401 token_revoked` response for the selected account, the proxy MUST attempt one
forced token refresh and retry on the same account if refresh succeeds. If the
refreshed retry returns 401, or refresh proves the account credentials permanently
unusable, the proxy MUST classify and record the account failure, exclude that
account from the current movable compact request, and try another eligible
account. The proxy MUST NOT surface the account-local 401 before exhausting
eligible accounts. Requests bound to an account-owned continuity artifact MUST
remain on that account and fail closed.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` before and after forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` before and after forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required` and excluded from further attempts

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the transport does not mark it as a generic same-contract transport retry

#### Scenario: Permanent refresh failure uses another account

- **GIVEN** at least two accounts are eligible for a movable compact request
- **AND** the selected account returns `401 token_revoked`
- **AND** forced refresh proves that account's credentials permanently unusable
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the other account
- **AND** the dead account is marked `reauth_required`
- **AND** the dead account is excluded from the request's remaining attempts

#### Scenario: Permanent refresh failure preserves account ownership

- **GIVEN** a compact request is bound to an account-owned continuity artifact
- **AND** that owner returns `401 token_revoked`
- **AND** forced refresh proves the owner's credentials permanently unusable
- **WHEN** another account is otherwise eligible
- **THEN** the proxy MUST NOT move the compact request to that account
- **AND** the owner is marked `reauth_required`
