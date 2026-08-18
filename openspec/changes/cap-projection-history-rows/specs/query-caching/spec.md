# query-caching Delta

## MODIFIED Requirements

### Requirement: Projection history reads are bounded per account
The dashboard projections history fetch MUST NOT widen every account's
lookback to the widest account window. On PostgreSQL the bulk usage-history
read MUST bound rows per account by that account's own window cutoff, and
MUST additionally bound each account's slice to a newest-first per-account
row cap supplied by the projections caller. Because live snapshot ingestion
writes a row per proxied request whenever the usage fingerprint changes, no
fixed row cap alone can guarantee coverage of a fixed time window; the
fetch MUST therefore exempt rows inside the configured pace-smoothing
window (the projections caller supplies its start as an uncapped recent
floor) so every row the equal-weight smoothing mean consumes is returned
regardless of write density, while the cap MUST still bound the rows older
than that floor. The cap MUST be sized to cover the remaining tail-weighted
consumers' lookback (the recent-burn EWMA window) at the ingestor's minimum
per-account write interval. Returned slices MUST keep the newest in-cutoff
rows and MUST remain ordered oldest-first. For accounts whose in-cutoff
rows do not exceed the cap, the returned histories MUST equal the previous
shared-floor fetch after the existing per-account trimming; for accounts
over the cap, the returned history MUST be exactly the union of every
in-cutoff row at or after the uncapped recent floor and the newest cap-many
in-cutoff rows older than the floor.

#### Scenario: One weekly account does not widen the fetch for short-window accounts
- **GIVEN** one account with a 7-day window and several accounts with 5-hour windows
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** rows for the 5-hour accounts MUST be bounded by their own cutoff in SQL
- **AND** each account's resulting history slice MUST equal the slice the shared-floor fetch produced after per-account trimming

#### Scenario: A dense account returns only its newest rows
- **GIVEN** an account whose in-cutoff usage-history rows exceed the per-account row cap
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** the account's slice MUST be exactly the in-cutoff rows at or after the uncapped recent floor plus the newest cap-many in-cutoff rows older than the floor, ordered oldest-first
- **AND** accounts whose in-cutoff rows do not exceed the cap MUST return their full trimmed slice unchanged

#### Scenario: A write burst inside the smoothing window is never truncated
- **GIVEN** an account that wrote more usage-history rows inside the configured pace-smoothing window than the per-account row cap
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** every in-cutoff row at or after the smoothing-window start MUST be returned
- **AND** the weekly-pace smoothed values MUST equal the values the uncapped fetch would produce

#### Scenario: Capped probes stay index-only
- **GIVEN** usage history rows for multiple accounts and a populated visibility map
- **WHEN** the capped per-account probe shape is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST serve each probe as an Index Only Scan over the covering indexes with no sequential scan of `usage_history`

#### Scenario: SQLite snapshot cache keeps the shared floor
- **GIVEN** the SQLite backend serves the projections history fetch through its snapshot cache
- **WHEN** per-account cutoffs and a per-account row cap are supplied
- **THEN** the SQLite read MAY keep the shared floor and MAY ignore the row cap
- **AND** per-account trimming in the caller MUST still bound each account's slice
