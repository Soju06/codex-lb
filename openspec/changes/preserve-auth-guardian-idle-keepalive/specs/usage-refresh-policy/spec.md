## MODIFIED Requirements

### Requirement: Proactive active account credential refresh

Codex-LB SHALL periodically scan account credentials in the background. Accounts with status `active` or `paused` SHALL be eligible for proactive credential refresh; accounts with status `reauth_required` or `deactivated` SHALL NOT be selected. Guardian age eligibility MUST require `last_refresh` to be strictly older than twelve hours. This keepalive window MUST remain independent of the request-preflight access-token freshness window. Candidate selection and the fresh per-account recheck MUST apply the same guardian age and status conditions. Once the fresh row is admitted, the worker MUST force credential refresh without applying the request-preflight age gate again.

The six-hour scan cadence bounds only the time until the next eligibility scan. Each pass MUST exclude accounts in active failure backoff and MUST admit no more than the fixed 100-account batch, ordered by oldest `last_refresh`; an eligible account outside that batch can wait through additional scans before refresh.

Proactive credential refresh MUST NOT change a paused account's routing eligibility: a paused account remains excluded from request routing regardless of refresh outcome, except that a permanent refresh failure transitions the account to its documented permanent-failure status the same way it does for active accounts. The proactive refresh scheduler SHALL be enabled by default with zero required configuration. Whether a refresh pass runs SHALL be decided by the dashboard setting `auth_guardian_enabled` (a nullable `dashboard_settings` column; NULL inherits the deprecated `CODEX_LB_AUTH_GUARDIAN_ENABLED` environment variable, then the default `true`), exposed with provenance on `GET`/`PUT /api/settings`. The scheduler loop SHALL always start; each refresh pass SHALL read the effective value from the dashboard-settings snapshot at the start of the pass and SHALL skip the pass while it is `false`, so a change made in the dashboard applies on the next pass on every replica without a restart. The multi-replica leader guard remains a precondition for any refresh work.

#### Scenario: Idle active account crosses the keepalive window

- **GIVEN** an account has status `active` and its `last_refresh` is more than twelve hours old
- **WHEN** Auth Guardian runs on the elected leader and admits the account
- **THEN** Codex-LB refreshes it without requiring request traffic
- **AND** request-preflight freshness does not suppress the keepalive exchange

#### Scenario: Account at the keepalive boundary is skipped

- **GIVEN** an account has status `active` or `paused` and was refreshed exactly twelve hours ago or more recently
- **WHEN** Auth Guardian selects candidates
- **THEN** the account is not selected

#### Scenario: Request freshness changes do not change idle keepalive

- **GIVEN** the request-preflight freshness window changes
- **WHEN** Auth Guardian evaluates an active or paused account more than twelve hours after its last refresh
- **THEN** guardian age eligibility remains true independently of request freshness

#### Scenario: Fresh row prevents a redundant refresh

- **GIVEN** an account was selected as older than twelve hours
- **AND** another owner refreshes it before the worker reads the fresh account row
- **WHEN** the worker rechecks that row
- **THEN** it skips the credential exchange if the account is no longer keepalive-eligible

#### Scenario: Eligible account is outside the per-pass batch

- **GIVEN** more than 100 accounts are keepalive-eligible
- **AND** an account is outside the first 100 after oldest-first ordering and exclusion of active failure backoff
- **WHEN** Auth Guardian runs the next leader-gated scan
- **THEN** that account is not admitted during that pass
- **AND** it remains eligible for a later scan

#### Scenario: Idle paused account keeps its refresh token alive

- **GIVEN** an account has status `paused` and was refreshed more than twelve hours ago
- **WHEN** Auth Guardian runs on the elected leader and admits the account
- **THEN** Codex-LB refreshes its credentials
- **AND** it remains paused and excluded from request routing

#### Scenario: Known-bad credentials are not refreshed

- **GIVEN** an account has status `reauth_required` or `deactivated` and was refreshed more than twelve hours ago
- **WHEN** Auth Guardian selects candidates
- **THEN** it is not selected

#### Scenario: Guardian runs on a default install

- **GIVEN** a single-replica deployment with no `CODEX_LB_AUTH_GUARDIAN_*` configuration and no dashboard value for `auth_guardian_enabled`
- **WHEN** the scheduler is built
- **THEN** it is enabled and its passes run

#### Scenario: Dashboard pause applies on the next pass without a restart

- **GIVEN** the scheduler was started with `auth_guardian_enabled` effectively `true`
- **WHEN** an operator sets it to `false` in the dashboard
- **THEN** the next refresh pass skips without refreshing any account
- **AND** setting it back to `true`, or clearing it to inherit `true`, enables the following pass without restarting a replica

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_AUTH_GUARDIAN_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `auth_guardian_enabled` to `true` in the dashboard
- **THEN** refresh passes run and provenance source is `dashboard`
- **AND** clearing the dashboard value returns to the environment value with source `env`
