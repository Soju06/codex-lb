## MODIFIED Requirements

### Requirement: Proactive active account credential refresh

Codex-LB SHALL periodically refresh account credentials in the background when an account's last refresh is older than a configured maximum age. Accounts with status `active` or `paused` SHALL be eligible for proactive credential refresh; accounts with status `reauth_required` or `deactivated` SHALL NOT be selected. Proactive credential refresh MUST NOT change a paused account's routing eligibility: a paused account remains excluded from request routing regardless of refresh outcome, except that a permanent refresh failure transitions the account to its documented permanent-failure status the same way it does for active accounts. The proactive refresh scheduler SHALL be enabled by default with zero required configuration. Whether a refresh pass runs SHALL be decided by the dashboard setting `auth_guardian_enabled` (a nullable `dashboard_settings` column; NULL inherits the deprecated `CODEX_LB_AUTH_GUARDIAN_ENABLED` environment variable, then the default `true`), exposed with provenance on `GET`/`PUT /api/settings`. The scheduler loop SHALL always start; each refresh pass SHALL read the effective value from the dashboard-settings snapshot at the start of the pass and SHALL skip the pass while it is `false`, so a change made in the dashboard applies on the next pass on every replica without a restart. The multi-replica leader guard remains a precondition for any refresh work.

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

- **GIVEN** a single-replica deployment with no `CODEX_LB_AUTH_GUARDIAN_*` configuration and no dashboard value for `auth_guardian_enabled`
- **WHEN** the Auth Guardian scheduler is built
- **THEN** the scheduler is enabled and its passes run

#### Scenario: Dashboard pause applies on the next pass without a restart

- **GIVEN** the scheduler was started with `auth_guardian_enabled` effectively `true`
- **WHEN** an operator sets `auth_guardian_enabled` to `false` in the dashboard
- **THEN** the next refresh pass skips without refreshing any account
- **AND** when the operator sets it back to `true` (or clears it so the inherited `true` applies) the pass after that refreshes stale accounts again
- **AND** no replica was restarted

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_AUTH_GUARDIAN_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `auth_guardian_enabled` to `true` in the dashboard
- **THEN** refresh passes run and `provenance.auth_guardian_enabled.source` is `dashboard`
- **AND** clearing the dashboard value returns to the environment value (`source` `env`)

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. When leader election is disabled, the guardian MUST detect multi-replica operation dynamically from live bridge ring membership (members with a heartbeat within the staleness threshold) in addition to the static instance ring, MUST skip the refresh pass when more than one live replica is detected, and MUST log a warning identifying the leader-election setting. The dashboard setting `auth_guardian_enabled` MUST NOT override this gate: with a static instance ring of more than one member and leader election disabled, every pass skips whatever the dashboard value is, and `GET /api/settings` SHALL report `auth_guardian_blocked_by_topology` as `true` so the dashboard can show why the guardian is idle.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Dynamically registered replicas without leader election

- **GIVEN** two replicas registered in `bridge_ring_members` with live heartbeats
- **AND** the static instance ring is empty
- **AND** leader election is disabled
- **WHEN** an Auth Guardian tick runs on either replica
- **THEN** the guardian performs no refresh work
- **AND** logs a warning identifying the leader-election setting

#### Scenario: Dashboard cannot enable the guardian in a multi-replica ring without leader election

- **GIVEN** a static instance ring of two members and leader election disabled
- **AND** `auth_guardian_enabled` is `true` in the dashboard
- **WHEN** an Auth Guardian tick runs
- **THEN** the guardian performs no refresh work and logs a warning identifying the leader-election setting
- **AND** `GET /api/settings` reports `auth_guardian_blocked_by_topology` as `true` while the effective `auth_guardian_enabled` stays `true`
