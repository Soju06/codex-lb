## MODIFIED Requirements

### Requirement: Projection history reads are bounded per account
The dashboard projections history fetch MUST NOT widen every account's
lookback to the widest account window. On PostgreSQL and SQLite the bulk usage-history
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
decay rather than to a time window at an assumed write cadence: the first
tail row only seeds the EWMA, so a cap-row tail performs cap-minus-one
updates, and with the EWMA smoothing factor in use the pre-tail state's
residual on the replayed rate MUST be bounded by the retained weight after
cap-minus-one updates times the largest per-second sample slope (below
about 1e-12 percent per second at the theoretical 100-percent-per-second
step). The EWMA advances once per distinct recorded second (its epoch
resolution), so that bound holds whenever the returned tail spans at least
cap-many distinct recorded seconds; a tail packed into fewer distinct
seconds (a same-second write burst older than the floor) MAY diverge from
the full replay. Returned slices MUST keep the
newest in-cutoff rows and MUST remain ordered oldest-first. For accounts
whose in-cutoff rows do not exceed the cap, the returned histories MUST
equal the shared-floor fetch after the existing per-account trimming; for
accounts over the cap, the returned history MUST be exactly the union of
every in-cutoff row at or after the uncapped recent floor and the newest
cap-many in-cutoff rows older than the floor. Consumers that weigh every
sample in a fixed time window equally MUST read only rows at or after the
floor and MUST produce values identical to the uncapped fetch; consumers
that replay a count-decaying EWMA MAY read the capped tail and, whenever
the tail spans at least cap-many distinct recorded seconds, MUST produce an
EWMA rate equal to the uncapped fetch within that residual bound (an
absolute bound on the rate); fields derived from the rate (burn rate, risk,
exhaustion ETA) MUST agree within that residual propagated through their
formulas (the burn rate scales it by seconds-until-reset over remaining
percent), and the exhaustion ETA fields, which are emitted only for a
strictly positive rate, MAY be absent from the capped replay when the
uncapped replay retains a positive ghost rate below the residual (an
account flat at its limit).

#### Scenario: One weekly account does not widen the fetch for short-window accounts
- **GIVEN** one account with a 7-day window and several accounts with 5-hour windows
- **WHEN** the projections history fetch runs on PostgreSQL or SQLite
- **THEN** rows for the 5-hour accounts MUST be bounded by their own cutoff in SQL
- **AND** each account's resulting history slice MUST equal the slice the shared-floor fetch produced after per-account trimming

#### Scenario: A dense account returns only its newest rows
- **GIVEN** an account whose in-cutoff usage-history rows exceed the per-account row cap
- **WHEN** the projections history fetch runs on PostgreSQL or SQLite
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
- **WHEN** the projections history fetch runs on PostgreSQL or SQLite
- **THEN** every in-cutoff row at or after the floor MUST be returned
- **AND** the weekly-pace smoothed values and fleet burn rate MUST equal the values the uncapped fetch would produce

#### Scenario: EWMA consumers agree with the full replay over the tail
- **GIVEN** an account with thousands of in-cutoff rows older than the floor
- **AND** the newest cap-many of those rows span at least cap-many distinct recorded seconds
- **WHEN** depletion or the weekly-pace recent burn rate is computed from the capped fetch and from the uncapped fetch
- **THEN** the EWMA rates MUST agree within the retained weight after cap-minus-one updates times the largest per-second sample slope in the history (an absolute bound on the rate)
- **AND** burn rate, risk, and exhaustion ETA MUST agree within that residual propagated through their formulas
- **AND** when a usage drop or window reset lands inside the returned tail the results MUST be identical

#### Scenario: The seed row bounds the tail residual
- **GIVEN** an account whose rows older than the floor are one step from zero to a high usage followed by cap-many flat rows one recorded second apart, so the uncapped replay retains a positive ghost rate while the capped tail decays to exactly zero
- **WHEN** depletion is computed from the capped fetch and from the uncapped fetch
- **THEN** the rates MAY differ, and the difference MUST NOT exceed the retained weight after cap-minus-one updates times the step's per-second slope
- **AND** the burn rate MAY differ by that residual scaled by seconds-until-reset over remaining percent

#### Scenario: A saturated account may lose its exhaustion ETA under the capped fetch
- **GIVEN** an account that reached its limit and has held a flat usage for longer than the floor, so the uncapped replay still carries a positive ghost rate that has decayed below the residual while the capped tail replays only flat rows
- **WHEN** depletion is computed from the capped fetch and from the uncapped fetch
- **THEN** risk and burn rate MUST be identical
- **AND** the capped replay MAY report no exhaustion ETA where the uncapped replay reports an immediate one, because the ETA is emitted only for a strictly positive rate

#### Scenario: A same-second write burst older than the floor bounds the tail guarantee
- **GIVEN** an account whose newest rows older than the floor were written several per recorded second, so the cap-many returned tail rows span fewer distinct recorded seconds than the cap
- **WHEN** depletion is computed from the capped fetch and from the uncapped fetch
- **THEN** the EWMA replays MAY diverge, because each recorded second contributes one EWMA update regardless of how many rows share it
- **AND** a tail whose rows span cap-many distinct recorded seconds MUST meet the residual bound however many rows share each second

#### Scenario: Capped probes stay index-only
- **GIVEN** usage history rows for multiple accounts and a populated visibility map
- **WHEN** the capped per-account probe shape is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST serve each probe as an Index Only Scan over the covering indexes with no sequential scan of `usage_history`

#### Scenario: SQLite snapshot cache keeps the shared floor
- **GIVEN** an uncapped history fetch on the SQLite backend served through its snapshot cache
- **WHEN** per-account cutoffs are supplied without a per-account row cap
- **THEN** the SQLite snapshot-cache read MAY keep the shared floor and MAY ignore cutoffs
- **AND** per-account trimming in the caller MUST still bound each account's slice

#### Scenario: SQLite capped probes use composite index without temporary B-tree
- **GIVEN** usage history rows for multiple accounts in a SQLite database
- **WHEN** the capped per-account probe shape is EXPLAINed on SQLite
- **THEN** the plan MUST serve each probe using `idx_usage_window_account_latest` or `idx_usage_window_account_time_covering` (or raw-window twins) and MUST NOT use a temporary B-tree for sorting
