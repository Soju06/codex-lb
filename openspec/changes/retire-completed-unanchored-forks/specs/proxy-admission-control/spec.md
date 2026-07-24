## ADDED Requirements

### Requirement: Completed request-scoped forks return account stream capacity

Account-local stream admission MUST count reusable live upstream work rather
than completed request-scoped fork history. When an ordinary unanchored fork
becomes quiescent after successful completion, its stream lease MUST be
released through full lane retirement without waiting for idle eviction,
stale-lease reclaim, account-cap pressure, or process restart.

#### Scenario: Sequential forks do not exhaust the account cap

- **GIVEN** sequential unanchored requests repeatedly fork from one active
  canonical session
- **AND** each fork completes before the next request begins
- **WHEN** the number of completed forks exceeds the account stream cap
- **THEN** each completed fork has already returned its stream lease
- **AND** later eligible work does not receive `account_stream_cap` because of
  those completed forks

#### Scenario: Active fork remains counted

- **GIVEN** an unanchored fork still owns pending, queued, admitted, or reserved work
- **WHEN** account-local stream pressure is measured
- **THEN** the fork continues to hold its stream lease
