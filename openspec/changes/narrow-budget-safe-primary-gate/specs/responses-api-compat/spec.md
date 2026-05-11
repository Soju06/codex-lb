## MODIFIED Requirements
### Requirement: Responses routing prefers budget-safe accounts
When serving Responses routes, the service MUST prefer eligible accounts whose
primary usage window is still below the configured budget threshold over
eligible accounts already above that primary-window threshold. Secondary-window
usage MAY be used as a routing strategy signal, but MUST NOT by itself exclude
an account from the budget-safe candidate set. If no below-primary-threshold
candidate exists, the service MAY fall back to pressured candidates, but the
`usage_weighted` degraded fallback MUST prefer lower primary-window pressure
before lower secondary-window usage.

#### Scenario: Fresh Responses request keeps weekly-pressured accounts eligible
- **WHEN** `/backend-api/codex/responses`, `/backend-api/codex/responses/compact`, `/v1/responses`, or `/v1/responses/compact` selects among multiple eligible active accounts
- **AND** one candidate is above the secondary-window usage threshold but below the primary-window usage threshold
- **AND** another candidate is above the primary-window usage threshold
- **THEN** the secondary-only pressured candidate remains eligible for the budget-safe selection set

#### Scenario: Fresh Responses fallback avoids the most primary-pressured account
- **WHEN** `/backend-api/codex/responses`, `/backend-api/codex/responses/compact`, `/v1/responses`, or `/v1/responses/compact` selects among multiple eligible active accounts with `usage_weighted` routing
- **AND** every candidate is above the configured primary-window budget threshold
- **AND** one candidate has lower secondary-window usage but higher primary-window usage
- **THEN** the account with lower primary-window usage is chosen first
