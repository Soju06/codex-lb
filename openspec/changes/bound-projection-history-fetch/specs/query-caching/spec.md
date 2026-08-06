# query-caching Delta

## ADDED Requirements

### Requirement: Projection history reads are bounded per account
The dashboard projections history fetch MUST NOT widen every account's
lookback to the widest account window. On PostgreSQL the bulk usage-history
read MUST bound rows per account by that account's own window cutoff; the
returned per-account histories MUST equal the previous shared-floor fetch
after the existing per-account trimming.

#### Scenario: One weekly account does not widen the fetch for short-window accounts
- **GIVEN** one account with a 7-day window and several accounts with 5-hour windows
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** rows for the 5-hour accounts MUST be bounded by their own cutoff in SQL
- **AND** each account's resulting history slice MUST equal the slice the shared-floor fetch produced after per-account trimming

#### Scenario: SQLite snapshot cache keeps the shared floor
- **GIVEN** the SQLite backend serves the projections history fetch through its snapshot cache
- **WHEN** per-account cutoffs are supplied
- **THEN** the SQLite read MAY keep the shared floor
- **AND** per-account trimming in the caller MUST still bound each account's slice
