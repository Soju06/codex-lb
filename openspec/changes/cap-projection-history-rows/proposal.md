## Why

Live snapshot ingestion appends a `usage_history` row whenever an
account's usage fingerprint moves, so a busy account's 7-day window holds
tens of thousands of rows while the dashboard projection consumers only
read the recent tail. The first cut of this change bounded the PostgreSQL
bulk read to the newest 4320 rows per account, sized to cover the 6-hour
recent-burn EWMA window at a presumed 5-second write throttle. On the
reference deployment that cap still binds on every account (all weekly-only,
history sourced from the primary stream): ~82k rows per dashboard poll,
polled several times a minute while a tab is open, each row hydrated
through SQLAlchemy `Row` attribute access and hashed field-by-field with
`blake2b(repr(...))` for the depletion cache signature. That work is ~5% of
the single worker core's GIL time and buys nothing the response can show:
with alpha 0.4 a sample's EWMA weight after `n` newer samples is `0.6**n`,
below double precision past a few dozen rows, and every equal-weight
consumer is already protected by the uncapped floor.

## What Changes

- Size the per-account row cap to the EWMA tail (64 rows) instead of a
  time window at a write cadence: rows older than the uncapped floor feed
  only the count-decaying EWMA consumers (depletion rate, weekly-pace recent
  burn), whose replay over the tail equals the full replay to floating-point
  noise regardless of write density.
- Widen the uncapped recent floor to the wider of the configured
  pace-smoothing window and the fixed 3-hour fleet-burn window, so every
  equal-weight consumer (smoothing mean, fleet burn sum/span, latest row)
  keeps exact inputs. Both bulk fetches carry the floor: weekly-only
  accounts sourced from the primary stream feed the weekly pace from the
  primary fetch.
- Hydrate PostgreSQL bulk-history rows by positional unpacking instead of
  per-column `Row` attribute lookups (same snapshot values, same
  oldest-first per-account ordering).
- Replace the field-by-field `blake2b(repr(...))` depletion cache content
  digest with a single fixed-width process-local hash over every row's
  value-bearing fields. The signature stays bounded, still detects in-place
  corrections that leave the endpoints and row count intact, and the cache
  it guards is process memory only.
- SQLite keeps its shared-floor snapshot cache and its own cache digest,
  and ignores the cap and floor the same way it ignores per-account cutoffs.
- No schema change: the existing covering indexes already serve the capped
  probes index-only.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the projections history bulk read MUST bound each
  account's rows older than the uncapped floor to a fixed EWMA tail, MUST
  exempt the wider of the pace-smoothing and fleet-burn windows from the
  cap on every projections fetch, MUST keep floor-covered consumers exact
  and tail-weighted consumers equivalent within floating-point tolerance,
  and the depletion cache MUST use a fixed-width content signature.

## Impact

`app/modules/dashboard/service.py` (tail cap constant, floor derivation),
`app/modules/usage/repository.py` (positional hydration in the bulk
fetch loops), `app/modules/usage/depletion_service.py` (content signature),
unit and PostgreSQL regression coverage. Dashboard values are unchanged
(equal-weight consumers exact; EWMA-derived fields equal to within
floating-point noise) — no API, response-schema, setting, migration, or
dashboard UI change.
