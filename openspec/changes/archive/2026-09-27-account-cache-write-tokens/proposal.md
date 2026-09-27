## Why

Upstream cache-write tokens currently receive the ordinary input price.
Usage logging and API-key settlement retain cache reads but discard the
separate write count, so refreshing model prices cannot correct the undercount.

## What Changes

- Preserve the upstream write count through logging and quota settlement.
- Import cache-write prices from the existing model-price sources and snapshot.
- Charge writes instead of ordinary input, using the applicable tier/context rate.
- Persist a nullable write count for future cost reconstruction.
- Preserve historical unknown counts and existing non-NULL costs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-metadata`: retain write prices and counts, and charge input categories once.
- `api-keys`: settle cost limits using the complete input usage.

## Impact

Usage parsing, pricing, request logs, API-key cost accounting and one additive
database migration. No new setting, public route, dashboard control or retry
policy. Existing price-only changes do not implement this accounting dimension.
