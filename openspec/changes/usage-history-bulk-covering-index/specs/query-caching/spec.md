# query-caching Delta

## ADDED Requirements

### Requirement: Projection history bulk reads are index-covered on PostgreSQL
The columns selected by the dashboard projections bulk usage-history fetch
MUST be fully covered by an index matching each of its predicate shapes on
PostgreSQL — the coalesced-primary window shape and the explicit raw-window
shape — so the read can be planned as an index-only scan without per-row
heap fetches. Non-PostgreSQL backends MUST keep the same-named indexes for
schema parity but MAY omit the covering payload.

#### Scenario: Primary-window bulk fetch plans as an index-only scan
- **GIVEN** usage history rows exist for multiple accounts with `NULL` and `'primary'` windows
- **AND** the table's visibility map is populated (`VACUUM ANALYZE` has run since the rows were written; with an empty visibility map the planner MAY prefer a plain Index Scan on a cheaper non-covering index)
- **WHEN** the bulk history fetch shape for `window="primary"` is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST be an Index Only Scan over the covering index whose keys are `(coalesce("window",'primary'), account_id, recorded_at)`
- **AND** the covering payload MUST carry the raw `"window"` column so the coalesce qual does not disqualify the index-only path
- **AND** the fetched rows MUST equal the non-covered read (the same fetch executed with index-only scans disabled), up to ordering among rows tied on the query's sort key

#### Scenario: Raw secondary-window bulk fetch plans as an index-only scan
- **GIVEN** usage history rows exist for multiple accounts with `'secondary'` windows
- **AND** the table's visibility map is populated (`VACUUM ANALYZE` has run since the rows were written)
- **WHEN** the bulk history fetch shape for `window="secondary"` is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST be an Index Only Scan over the covering index whose keys are `("window", account_id, recorded_at)`

#### Scenario: Covering indexes are created concurrently and repair invalid leftovers
- **GIVEN** a PostgreSQL database where a previous `CREATE INDEX CONCURRENTLY` for a covering index was interrupted and left an invalid index
- **WHEN** the covering-index migration is applied
- **THEN** the invalid leftover MUST be dropped and the index rebuilt concurrently
- **AND** re-running the migration MUST complete without duplicate-index failure

#### Scenario: Missing covering index fails schema drift checks
- **GIVEN** a database whose `usage_history` table lacks one of the covering indexes
- **WHEN** the schema drift check runs
- **THEN** it MUST report the missing index by name

### Requirement: Append-heavy usage-history visibility is maintained for the covering path
Covering indexes alone do not keep the bulk read heap-free: `usage_history`
is append-heavy (high-frequency inserts, no updates or deletes), and with
PostgreSQL's default insert-driven autovacuum trigger the freshly appended
pages stay outside the visibility map long enough that "index-only" scans
degrade into per-row heap fetches. The `usage_history` table on PostgreSQL
MUST therefore carry per-table insert-driven autovacuum tuning
(`autovacuum_vacuum_insert_scale_factor = 0.02`,
`autovacuum_vacuum_insert_threshold = 50000`,
`autovacuum_analyze_scale_factor = 0.02`, matching the tuning already
applied to the other insert-heavy tables by `20260717_000000`) so the
visibility map stays fresh and the covering read path remains index-only.
Non-PostgreSQL backends MUST NOT be affected (no visibility map).

#### Scenario: Migration sets the insert-driven autovacuum parameters
- **GIVEN** a PostgreSQL database migrated past the covering-index revision
- **WHEN** the autovacuum tuning revision is applied
- **THEN** `usage_history` reloptions MUST include `autovacuum_vacuum_insert_scale_factor=0.02`, `autovacuum_vacuum_insert_threshold=50000`, and `autovacuum_analyze_scale_factor=0.02`
- **AND** downgrading the revision MUST reset those three parameters

#### Scenario: Re-applying over a manually tuned deployment is harmless
- **GIVEN** a PostgreSQL deployment where the identical autovacuum settings were already applied manually (the reference deployment's hotfix)
- **WHEN** the autovacuum tuning revision is applied
- **THEN** the migration MUST complete without error and leave the same settings in place
