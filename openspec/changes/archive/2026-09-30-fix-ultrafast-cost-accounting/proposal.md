## Why

The shared cost calculator treats Astra Ultrafast as standard pricing. This
undercounts request costs and API-key cost-limit settlement even when upstream
confirms the billable tier is Ultrafast.

## What Changes

- Recognize explicit short- and long-context Ultrafast input, cached-input and
  output prices in the existing calculator, catalogs and snapshots.
- Bundle Astra's published prices for offline startup and retain these fields
  during compatible partial metadata refreshes.
- Verify streaming and non-streaming request costs and limits against the actual
  upstream tier rather than the requested tier.
- Preserve historical non-NULL costs and subscription quota percentages.

## Capabilities

### Modified Capabilities

- `upstream-metadata`: validated Ultrafast prices and offline fallbacks.
- `api-keys`: actual-tier Ultrafast request costs and cost-limit settlement.

## Impact

Only existing pricing/catalog data and related regression tests change. No
schema, configuration, frontend or dependency change. Cache-write accounting is
separate work in PR #2504 and is not introduced here.
