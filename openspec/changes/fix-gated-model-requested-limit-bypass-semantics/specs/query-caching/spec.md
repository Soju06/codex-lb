## ADDED Requirements

### Requirement: Gated model selection keeps requested quota windows isolated
When a request targets a gated model whose canonical additional quota is known, account selection SHALL rank and budget candidates using persisted usage windows for that requested additional quota only. Missing requested additional-quota windows SHALL NOT fall back to ordinary account usage windows for requested-limit ranking, budget-safety checks, or relative-availability scoring.

#### Scenario: Missing requested secondary window does not borrow ordinary secondary usage
- **GIVEN** account A has requested additional primary usage but no requested additional secondary usage
- **AND** account A has ordinary secondary usage near exhaustion
- **AND** account B has worse requested additional primary usage
- **WHEN** selecting an account for the gated model with requested-limit routing
- **THEN** account A is not penalized by its ordinary secondary usage for requested-limit ranking

#### Scenario: Requested secondary window is used when present
- **GIVEN** an account has requested additional primary and secondary usage windows
- **WHEN** selecting an account for the gated model with requested-limit routing
- **THEN** both requested additional windows may contribute to ranking and budget-safety decisions

#### Scenario: Requested reset window drives relative availability
- **GIVEN** account A has an ordinary secondary window that resets later than its requested additional quota
- **AND** account B has an ordinary secondary window that resets sooner than its requested additional quota
- **WHEN** selecting an account for the gated model with relative-availability routing
- **THEN** requested-limit scoring uses each account's requested additional-quota reset window instead of the ordinary secondary reset window

### Requirement: Quota status bypass preserves cooldown backoff
When requested additional-quota data proves an account can serve a gated model despite persisted `QUOTA_EXCEEDED` account status, account selection MAY bypass the persisted quota status for that requested gated model. This bypass SHALL NOT bypass `cooldown_until`, pause, deactivation, rate-limit, or error-backoff gates.

#### Scenario: Requested quota bypass does not bypass cooldown
- **GIVEN** an account is `QUOTA_EXCEEDED`
- **AND** requested additional-quota data marks the account eligible for the gated model
- **AND** the account has `cooldown_until` in the future
- **WHEN** selecting an account for that gated model
- **THEN** the account is not selected until the cooldown expires

#### Scenario: Requested quota bypass can select a cooled eligible account
- **GIVEN** an account is `QUOTA_EXCEEDED`
- **AND** requested additional-quota data marks the account eligible for the gated model
- **AND** the account has no active cooldown, pause, deactivation, rate-limit, or error backoff
- **WHEN** selecting an account for that gated model
- **THEN** the persisted quota status does not by itself exclude the account
