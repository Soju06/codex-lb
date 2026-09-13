## MODIFIED Requirements

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` or `401 token_invalidated`
response for the selected account, the proxy MUST attempt one forced token
refresh and retry the compact request on that same account. If the refreshed
retry also returns `401`, the proxy MUST classify and record the account
failure, exclude that account from the current compact request, and try another
eligible account when one is available. If the forced refresh itself confirms
a permanent credential failure, the proxy MUST mark the selected account for
re-authentication, exclude it from the current compact request, and try another
eligible account when account ownership permits. The proxy MUST NOT surface an
account-local `401` before exhausting eligible accounts, and MUST NOT move a
file-pinned or continuity-pinned compact request to another account. When no
safe replacement is available, the proxy MUST preserve the terminal auth error
and settlement behavior.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required`
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry

#### Scenario: Permanent forced-refresh failure uses another account

- **GIVEN** at least two accounts are eligible for an account-neutral compact request
- **AND** the selected account returns an upstream authentication failure
- **WHEN** its forced refresh reports a permanent revoked-credential failure
- **THEN** the selected account is marked `reauth_required` and excluded
- **AND** the compact request succeeds from another eligible account when it completes
- **AND** the selected account's authentication error is not surfaced to the client

#### Scenario: Permanent forced-refresh failure preserves an account pin

- **GIVEN** a compact request is pinned to an account by file or continuity ownership
- **AND** that account returns an upstream authentication failure
- **WHEN** its forced refresh reports a permanent credential failure
- **THEN** the account is marked `reauth_required`
- **AND** the request is not sent to another account
- **AND** the terminal authentication error is surfaced after settlement
