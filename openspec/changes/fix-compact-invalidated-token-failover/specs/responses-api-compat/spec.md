## ADDED Requirements

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` response for the selected
account, the proxy MUST attempt one forced token refresh and retry the compact
request on that same account. If the refreshed retry also returns `401`, the
proxy MUST classify and record the account failure, exclude that account from
the current compact request, and try another eligible account when one is
available. The proxy MUST NOT surface the repeated account-local `401` to the
compact client before exhausting eligible accounts.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry
