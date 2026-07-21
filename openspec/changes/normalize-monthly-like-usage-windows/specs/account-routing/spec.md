## ADDED Requirements

### Requirement: Monthly-like quota remains a long-window routing signal

Account selection SHALL treat an authoritative observed window from 28 through
32 days as long-window quota pressure and MUST NOT expose its usage as the
short primary-window signal. Stored plan metadata MUST NOT by itself discard a
fresh monthly-like row, while a meaningfully newer non-monthly standard row
MUST supersede stale monthly history.

#### Scenario: Team monthly-only pressure is not a 5h signal

- **GIVEN** a Team account has a fresh 43800-minute quota row at 96% used
- **AND** it has no real short or weekly quota row
- **WHEN** the load balancer builds account state
- **THEN** short-window usage and duration are absent
- **AND** long-window usage is 96%
