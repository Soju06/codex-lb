## MODIFIED Requirements

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` or `401 token_invalidated`
response for the selected account, the proxy MUST attempt one forced token
refresh and retry the compact request on that same account. If the refreshed
retry also returns `401`, the proxy MUST classify and record the account
failure, exclude that account from the current compact request, and try another
eligible account when one is available. The proxy MUST NOT surface the repeated
account-local `401` to the compact client before exhausting eligible accounts.

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` for compact
  before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required`
- **AND** the selected account is excluded from further attempts for that
  compact request

### Requirement: Pre-visible proxy auth failures fail over after forced refresh

The proxy MUST treat repeated account-local authentication failures as
per-request account failures before any downstream-visible output is emitted.
When a proxy request on a non-compact surface retries with a refreshed token and
the refreshed retry still returns upstream `401 invalid_api_key` or
`401 token_invalidated`, the proxy MUST classify and record the selected account
failure, exclude that account from the current request, and try another eligible
account when one is available. The proxy MUST preserve the existing no-replay
rule after downstream-visible stream or websocket output has been emitted.

#### Scenario: Pre-visible token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a pre-visible proxy request
- **AND** the selected account returns `401 token_invalidated` before and after
  a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is marked `reauth_required`

#### Scenario: Non-replayable pre-visible auth failure still records the account

- **GIVEN** a pre-visible HTTP bridge continuation cannot be replayed safely
- **AND** the selected account returns `401 token_invalidated`
- **WHEN** the proxy forwards the terminal auth error instead of replaying
- **THEN** the selected account is marked `reauth_required`
- **AND** the unsafe continuation is not replayed
