## MODIFIED Requirements

### Requirement: Proactive active account credential refresh

Codex-LB SHALL periodically scan account credentials in the background.
Accounts with status `active` or `paused` SHALL be eligible for proactive
credential refresh; accounts with status `reauth_required` or `deactivated`
SHALL NOT be selected. Age eligibility MUST use the same shared proactive token
freshness predicate as request preflight (`should_refresh(last_refresh, now)`),
whose fixed window is eight days. Auth Guardian MUST NOT maintain an independent
maximum refresh age or shorten the shared window. Candidate selection and the
fresh per-account recheck MUST both use that predicate. Once the fresh row is
admitted, the worker MUST call `ensure_fresh(..., force=True)` to execute that
already-approved refresh without making a second age decision on a different
clock. The six-hour scan cadence bounds only the time until the next
eligibility scan. Each pass MUST exclude accounts in active failure backoff and
MUST admit no more than the fixed 100-account batch, ordered by oldest
`last_refresh`; an eligible account outside that batch can therefore wait
through additional scans before refresh.

Proactive credential refresh MUST NOT change a paused account's routing
eligibility: a paused account remains excluded from request routing regardless
of refresh outcome, except that a permanent refresh failure transitions the
account to its documented permanent-failure status the same way it does for
active accounts. The proactive refresh scheduler SHALL be enabled by default
with zero required configuration. Whether a refresh pass runs SHALL be decided
by the dashboard setting `auth_guardian_enabled` (a nullable
`dashboard_settings` column; NULL inherits the deprecated
`CODEX_LB_AUTH_GUARDIAN_ENABLED` environment variable, then the default
`true`), exposed with provenance on `GET`/`PUT /api/settings`. The scheduler
loop SHALL always start; each refresh pass SHALL read the effective value from
the dashboard-settings snapshot at the start of the pass and SHALL skip the
pass while it is `false`, so a change made in the dashboard applies on the next
pass on every replica without a restart. The multi-replica leader guard remains
a precondition for any refresh work.

#### Scenario: Idle active account crosses the shared refresh window

- **GIVEN** an account has status `active`
- **AND** its `last_refresh` is more than eight days old
- **WHEN** Auth Guardian runs on the elected leader
- **THEN** Codex-LB refreshes that account without requiring request traffic to
  select it first

#### Scenario: Account inside the shared refresh window is skipped

- **GIVEN** an account has status `active` or `paused`
- **AND** the shared request-preflight `should_refresh` policy says its
  credentials are still fresh
- **WHEN** Auth Guardian selects refresh candidates
- **THEN** the account is not selected
- **AND** no guardian-specific shorter age can make it eligible

#### Scenario: Eligible account is outside the per-pass batch

- **GIVEN** more than 100 accounts are stale and otherwise eligible
- **AND** an account is outside the first 100 after ordering by oldest
  `last_refresh` and excluding accounts in active failure backoff
- **WHEN** Auth Guardian runs the next leader-gated scan
- **THEN** that account is not selected during that pass
- **AND** it remains eligible for admission during a later scan

#### Scenario: Shared refresh-policy change applies to the guardian

- **GIVEN** the shared `should_refresh` policy's fixed window changes
- **WHEN** Auth Guardian evaluates an account at the same `last_refresh` and
  current time as request preflight
- **THEN** both paths reach the same freshness decision without a separate
  guardian threshold change

#### Scenario: Idle paused account keeps its refresh token alive

- **GIVEN** an account has status `paused`
- **AND** its `last_refresh` is more than eight days old
- **WHEN** Auth Guardian runs on the elected leader
- **THEN** Codex-LB refreshes that account's credentials
- **AND** the account's status remains `paused`
- **AND** the account remains excluded from request routing

#### Scenario: Known-bad credentials are not refreshed

- **GIVEN** an account has status `reauth_required` or `deactivated`
- **AND** the shared refresh-age predicate would otherwise consider it stale
- **WHEN** Auth Guardian selects refresh candidates
- **THEN** the account is not selected

#### Scenario: Guardian runs on a default install

- **GIVEN** a single-replica deployment with no
  `CODEX_LB_AUTH_GUARDIAN_*` configuration and no dashboard value for
  `auth_guardian_enabled`
- **WHEN** the Auth Guardian scheduler is built
- **THEN** the scheduler is enabled and its passes run

#### Scenario: Dashboard pause applies on the next pass without a restart

- **GIVEN** the scheduler was started with `auth_guardian_enabled` effectively
  `true`
- **WHEN** an operator sets `auth_guardian_enabled` to `false` in the dashboard
- **THEN** the next refresh pass skips without refreshing any account
- **AND** when the operator sets it back to `true` (or clears it so the
  inherited `true` applies) the pass after that refreshes stale accounts again
- **AND** no replica was restarted

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_AUTH_GUARDIAN_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `auth_guardian_enabled` to `true` in the dashboard
- **THEN** refresh passes run and `provenance.auth_guardian_enabled.source` is
  `dashboard`
- **AND** clearing the dashboard value returns to the environment value
  (`source` `env`)
