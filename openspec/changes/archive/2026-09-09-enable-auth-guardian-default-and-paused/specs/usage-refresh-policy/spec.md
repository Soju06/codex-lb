## MODIFIED Requirements

### Requirement: Proactive active account credential refresh

Codex-LB SHALL periodically refresh account credentials in the background when an account's last refresh is older than a configured maximum age. Accounts with status `active` or `paused` SHALL be eligible for proactive credential refresh; accounts with status `reauth_required` or `deactivated` SHALL NOT be selected. Proactive credential refresh MUST NOT change a paused account's routing eligibility: a paused account remains excluded from request routing regardless of refresh outcome, except that a permanent refresh failure transitions the account to its documented permanent-failure status the same way it does for active accounts. The proactive refresh scheduler SHALL be enabled by default with zero required configuration, and `CODEX_LB_AUTH_GUARDIAN_ENABLED=false` SHALL disable it. The multi-replica leader guard remains a precondition for any refresh work.

#### Scenario: Idle active account becomes stale

- **GIVEN** an account has status `active`
- **AND** its `last_refresh` is older than the configured Auth Guardian max age
- **WHEN** Auth Guardian runs on the elected leader
- **THEN** Codex-LB force-refreshes that account without requiring request traffic to select it first

#### Scenario: Idle paused account keeps its refresh token alive

- **GIVEN** an account has status `paused`
- **AND** its `last_refresh` is older than the configured Auth Guardian max age
- **WHEN** Auth Guardian runs on the elected leader
- **THEN** Codex-LB force-refreshes that account's credentials
- **AND** the account's status remains `paused`
- **AND** the account remains excluded from request routing

#### Scenario: Known-bad credentials are not refreshed

- **GIVEN** an account has status `reauth_required` or `deactivated`
- **AND** its `last_refresh` is older than the configured Auth Guardian max age
- **WHEN** Auth Guardian selects refresh candidates
- **THEN** the account is not selected

#### Scenario: Guardian runs on a default install

- **GIVEN** a single-replica deployment with no `CODEX_LB_AUTH_GUARDIAN_*` configuration
- **WHEN** the Auth Guardian scheduler is built
- **THEN** the scheduler is enabled
