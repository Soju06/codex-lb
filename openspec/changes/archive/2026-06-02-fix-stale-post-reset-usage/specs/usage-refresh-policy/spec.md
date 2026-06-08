## ADDED Requirements

### Requirement: Usage refresh does not trust elapsed reset windows

Background usage refresh MUST treat a latest usage row as stale when that row's `reset_at` timestamp is in the past, even when the row's `recorded_at` timestamp is still within the normal refresh interval.

#### Scenario: Past reset_at bypasses freshness

- **GIVEN** the latest usage row was recorded within the normal refresh interval
- **AND** that row's `reset_at` timestamp has already elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** the row is treated as stale
- **AND** codex-lb attempts a fresh upstream usage fetch

### Requirement: Blocked accounts refresh once their reset deadline elapses

When an account is `RATE_LIMITED` or `QUOTA_EXCEEDED` and its persisted `reset_at` timestamp has elapsed, background usage refresh MUST bypass the normal freshness interval so the account can recover from the upstream post-reset state. The bypass MUST NOT apply before the persisted reset deadline elapses.

#### Scenario: Quota-exceeded account with fresh primary row reaches reset deadline

- **GIVEN** an account is marked `QUOTA_EXCEEDED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **AND** the latest primary usage row is still within the normal refresh interval
- **WHEN** background usage refresh evaluates the account
- **THEN** codex-lb performs an upstream usage fetch instead of waiting for the primary row to age out

#### Scenario: Rate-limited account reaches reset deadline

- **GIVEN** an account is marked `RATE_LIMITED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** codex-lb performs an upstream usage fetch instead of waiting for the normal refresh interval
