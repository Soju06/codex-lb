# query-caching Delta

## MODIFIED Requirements

### Requirement: Projection history reads are bounded per account
The dashboard projections history fetch MUST NOT widen every account's
lookback to the widest account window. On PostgreSQL the bulk usage-history
read MUST bound rows per account by that account's own window cutoff, and
MUST additionally bound each account's rows older than an uncapped recent
floor to a newest-first per-account row cap supplied by the projections
caller. Because live snapshot ingestion writes a row per proxied request
whenever the usage fingerprint changes, no fixed row cap can guarantee
coverage of a fixed time window; the fetch MUST therefore exempt rows at or
after the uncapped recent floor from the cap so every row an equal-weight
consumer reads is returned regardless of write density. The projections
caller MUST derive the floor as the wider of the configured pace-smoothing
window and the weekly-pace fleet-burn window, and MUST supply the cap and
the floor on every projections bulk fetch, including the primary-window
fetch (weekly-only accounts sourced from the primary stream feed the weekly
pace from it). The cap MUST be sized to the tail-weighted consumers' EWMA
decay rather than to a time window at an assumed write cadence: with the
EWMA smoothing factor in use, a sample's weight after cap-many newer EWMA
updates MUST be below double precision. The EWMA advances once per distinct
recorded second (its epoch resolution), so the tail replay reproduces the
full replay within floating-point tolerance whenever the returned tail spans
at least cap-many distinct recorded seconds; a tail packed into fewer
distinct seconds (a same-second write burst older than the floor) MAY
diverge from the full replay. Returned slices MUST keep the
newest in-cutoff rows and MUST remain ordered oldest-first. For accounts
whose in-cutoff rows do not exceed the cap, the returned histories MUST
equal the shared-floor fetch after the existing per-account trimming; for
accounts over the cap, the returned history MUST be exactly the union of
every in-cutoff row at or after the uncapped recent floor and the newest
cap-many in-cutoff rows older than the floor. Consumers that weigh every
sample in a fixed time window equally MUST read only rows at or after the
floor and MUST produce values identical to the uncapped fetch; consumers
that replay a count-decaying EWMA MAY read the capped tail and MUST
produce rate-bearing values equal to the uncapped fetch within
floating-point tolerance (1e-12 relative or absolute) whenever the tail
spans at least cap-many distinct recorded seconds.

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

#### Scenario: Equal-weight consumers are exempt from the cap on every fetch
- **GIVEN** the configured pace-smoothing window and the fixed weekly-pace fleet-burn window
- **WHEN** the projections history fetch runs for the primary and the secondary window
- **THEN** both bulk fetches MUST supply the per-account row cap
- **AND** both MUST supply an uncapped recent floor equal to now minus the wider of the two windows
- **AND** a weekly-only account whose history source is the primary stream MUST receive the same cap and floor on the primary fetch whether or not the caller requested primary-window depletion

#### Scenario: A write burst inside an equal-weight window is never truncated
- **GIVEN** an account that wrote more usage-history rows inside the smoothing or fleet-burn window than the per-account row cap
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** every in-cutoff row at or after the floor MUST be returned
- **AND** the weekly-pace smoothed values and fleet burn rate MUST equal the values the uncapped fetch would produce

#### Scenario: EWMA consumers agree with the full replay over the tail
- **GIVEN** an account with thousands of in-cutoff rows older than the floor
- **AND** the newest cap-many of those rows span at least cap-many distinct recorded seconds
- **WHEN** depletion or the weekly-pace recent burn rate is computed from the capped fetch and from the uncapped fetch
- **THEN** the rate-bearing results MUST agree within 1e-12 (relative or absolute)
- **AND** when a usage drop or window reset lands inside the returned tail the results MUST be identical

#### Scenario: A same-second write burst older than the floor bounds the tail guarantee
- **GIVEN** an account whose newest rows older than the floor were written several per recorded second, so the cap-many returned tail rows span fewer distinct recorded seconds than the cap
- **WHEN** depletion is computed from the capped fetch and from the uncapped fetch
- **THEN** the EWMA replays MAY diverge, because each recorded second contributes one EWMA update regardless of how many rows share it
- **AND** a tail whose rows span cap-many distinct recorded seconds MUST agree within 1e-12 however many rows share each second

#### Scenario: Capped probes stay index-only
- **GIVEN** usage history rows for multiple accounts and a populated visibility map
- **WHEN** the capped per-account probe shape is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST serve each probe as an Index Only Scan over the covering indexes with no sequential scan of `usage_history`

#### Scenario: SQLite snapshot cache keeps the shared floor
- **GIVEN** the SQLite backend serves the projections history fetch through its snapshot cache
- **WHEN** per-account cutoffs, a per-account row cap, and an uncapped recent floor are supplied
- **THEN** the SQLite read MAY keep the shared floor and MAY ignore the row cap and the floor
- **AND** per-account trimming in the caller MUST still bound each account's slice

### Requirement: Dashboard overview memoizes per-account depletion EWMA state

`GET /api/dashboard/overview` MUST cache per-account EWMA depletion state in memory so repeated polls do not re-walk the full in-window `usage_history` slice in the depletion cache check when its content is unchanged. The attached compact content signature MUST be fixed-width regardless of history length (row count, first and latest row edge tuples, and one fixed-width content hash over every row's value-bearing fields); it MAY be process-local because the cache it guards is process memory only, and the cache MUST NOT retain a per-row signature structure. SQLite bulk history cache hits MUST avoid rebuilding or materializing the full cached history window when compact digest metadata proves older rows are unchanged; they MUST append newly inserted rows by monotonic row ID and reuse the cached grouped history for older rows. Repository-owned mutations that reassign or delete usage-history rows MUST clear the SQLite bulk history cache.

#### Scenario: Repeated polls with unchanged history reuse cached EWMA state
- **GIVEN** the dashboard service has previously computed depletion for an account
- **AND** a subsequent request supplies the same in-window history slice for that account with the same attached compact content signature
- **WHEN** depletion is recomputed for the dashboard response
- **THEN** the service MUST reuse the cached EWMA state for that account instead of replaying every history row
- **AND** the depletion metrics for that account MUST match the previously returned values for rate-bearing fields
- **AND** the cache hit check MUST compare fixed-width signature metadata and MUST NOT retain a per-row signature structure
- **AND** the service MUST prune cached depletion state for account/window keys that are absent from the current dashboard history set

#### Scenario: Memoized EWMA state is invalidated when a new usage row is appended
- **WHEN** a later dashboard request supplies the same account's in-window history with an additional row appended (a new `recorded_at` past the previous latest)
- **THEN** the service MUST rebuild the EWMA state from the new history slice
- **AND** the recomputed rate MUST reflect the newly observed sample

#### Scenario: Memoized EWMA state is invalidated when an older row ages out of the window
- **WHEN** a later dashboard request supplies the same account's in-window history with the earliest row dropped (because it has aged past the window cutoff)
- **THEN** the service MUST rebuild the EWMA state from the narrowed history slice
- **AND** the cached state from the wider window MUST NOT influence the recomputed rate

#### Scenario: Memoized EWMA state is invalidated when an existing usage row is corrected
- **WHEN** a later dashboard request supplies the same account's in-window history with the same row count and endpoints but a corrected `used_percent`, `reset_at`, or `window_minutes` value on an existing row (including a value becoming or ceasing to be absent)
- **THEN** the service MUST rebuild the EWMA state from the corrected history slice
- **AND** the recomputed rate-bearing metrics MUST reflect the corrected row content

#### Scenario: SQLite bulk history cache hit appends only new rows
- **GIVEN** a SQLite bulk usage-history query has already cached rows for an account/window set
- **WHEN** a later query uses a narrower `since` timestamp and the database only has new rows with IDs greater than the cached max ID
- **THEN** the repository fetches the new rows and appends them to the cached grouped history
- **AND** it does not materialize the older cached rows as snapshots when compact digest metadata proves they are unchanged

#### Scenario: Usage-history ownership mutation clears SQLite bulk history cache
- **WHEN** an account merge or delete operation updates or deletes `usage_history` rows
- **THEN** the repository clears the SQLite bulk history cache before serving future cached dashboard history reads
