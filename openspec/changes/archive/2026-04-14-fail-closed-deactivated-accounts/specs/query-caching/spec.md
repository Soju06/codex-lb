### ADDED Requirement: Deactivated upstream accounts fail closed
When the system receives a permanent upstream deactivation signal for an account, it MUST mark that account as `deactivated`, persist a deactivation reason, and remove the account from future routing eligibility until an operator explicitly reactivates it.

#### Scenario: Request-path account_deactivated error removes the account from the pool
- **WHEN** the proxy request path receives an upstream error with `error.code = "account_deactivated"`
- **THEN** the account is marked `deactivated`
- **AND** the deactivation reason is persisted
- **AND** future account selection excludes that account from the routing pool

#### Scenario: Usage-path deactivation code removes the account from the pool
- **WHEN** the usage refresh path receives `HTTP 401` with `error.code = "account_deactivated"`
- **THEN** the account is marked `deactivated`
- **AND** the deactivation reason is persisted
- **AND** future account selection excludes that account from the routing pool

#### Scenario: Usage-path deactivation message removes the account from the pool
- **WHEN** the usage refresh path receives `HTTP 401` whose error message states that the OpenAI account has been deactivated
- **AND** no structured error code is available
- **THEN** the account is marked `deactivated`
- **AND** the deactivation reason is persisted
- **AND** future account selection excludes that account from the routing pool

#### Scenario: Generic unauthorized usage errors stay non-terminal
- **WHEN** the usage refresh path receives `HTTP 401` without a permanent deactivation code or deactivation message
- **THEN** the system does not mark the account `deactivated`
- **AND** the existing retry or refresh-token recovery behavior remains in effect
