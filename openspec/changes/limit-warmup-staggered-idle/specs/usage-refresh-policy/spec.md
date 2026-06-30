## MODIFIED Requirements

### Requirement: Reset-confirmed limit warm-up

The system SHALL support an optional limit warm-up mechanism that is disabled by default. When enabled globally and for an account, background usage refresh MAY send one minimal upstream Responses request after it confirms that a selected quota window has moved from an exhausted sample to a newly available reset window.

The system SHALL also support a separate disabled-by-default staggered idle warm-up mode. When that mode is enabled globally and the account is opted in, background usage refresh MAY send one minimal upstream Responses request for an active account whose primary 5h usage window is fully unused. Idle warm-up attempts MUST be deduplicated per account/window/reset tuple and MUST be scheduled deterministically across the primary reset window instead of all firing immediately.

#### Scenario: Warm-up is skipped unless reset is confirmed
- **GIVEN** limit warm-up is enabled globally and for an account
- **AND** the account's previous usage sample for a selected window was exhausted
- **WHEN** background usage refresh records a newer sample for that window with `used_percent < 100` and a later `reset_at`
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Warm-up is opt-in and safe by default
- **GIVEN** background usage refresh is preparing to evaluate limit warm-up candidates
- **WHEN** global limit warm-up is disabled
- **OR** the account is not opted in
- **THEN** background usage refresh MUST NOT send warm-up traffic

#### Scenario: Idle warm-up is skipped unless explicitly enabled
- **GIVEN** an active opted-in account has a primary 5h usage window with `used_percent = 0`
- **WHEN** staggered idle warm-up is disabled globally
- **THEN** background usage refresh MUST NOT send idle warm-up traffic for that account

#### Scenario: Idle warm-up is staggered and deduplicated
- **GIVEN** staggered idle warm-up is enabled globally
- **AND** an active opted-in account has a primary 5h usage window with `used_percent = 0`
- **WHEN** background usage refresh evaluates that account before its deterministic stagger point
- **THEN** no warm-up request is sent yet
- **WHEN** background usage refresh evaluates the same account at or after its deterministic stagger point
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Warm-up uses fresh opt-in state after usage refresh
- **GIVEN** an account was loaded before a background usage refresh cycle
- **AND** the account's limit warm-up opt-in changes while the refresh cycle is running
- **WHEN** the scheduler evaluates warm-up candidates after writing usage samples
- **THEN** the scheduler MUST evaluate the latest persisted opt-in value rather than the stale in-session account object

#### Scenario: Warm-up respects unsafe account states
- **WHEN** an account is paused, deactivated, rate-limited, quota-exceeded, or in an auth-refresh failure path
- **THEN** limit warm-up MUST NOT send traffic for that account

#### Scenario: Warm-up attempts are durable and deduplicated
- **WHEN** multiple refresh workers observe the same account/window/reset candidate
- **THEN** the database permits at most one persisted attempt for that tuple
