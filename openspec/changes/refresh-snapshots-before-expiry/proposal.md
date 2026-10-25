# Refresh analytical snapshots before expiry

## Why
Scheduling the next refresh at the nominal freshness boundary leaves no time
for collection. Healthy queued refreshes can exhaust the reader's freshness grace
before their replacements are published. Starting work earlier avoids that gap
without extending the reader's freshness policy.

## What changes
- Reserve the existing collector timeout plus two scheduler ticks before expiry
  when scheduling the next collection.
- Preserve freshness metadata, publication fencing, failure handling and last-good
  values. Do not increase the freshness interval or add a setting.

## Impact
The snapshot publication schedule changes; no migration, accounting, inference or
request-time aggregation changes are needed. Qualify the deployed-source correction
separately from broader database-compatibility changes.
