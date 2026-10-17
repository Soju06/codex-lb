# Change: Surface reports cancellation totals

## Why

The reports API already returns cancellation totals, but the frontend parser drops them and the reports summary, daily table, and CSV export omit them. Operators therefore cannot distinguish cancelled requests from genuine errors on the Reports surface even though the owning metric contract requires cancellation counts alongside errors.

## What Changes

- Preserve `summary.totalCancelled` and `daily[].cancelledCount` when parsing reports responses.
- Zero-fill missing daily cancellation counts as `0` when constructing a complete date range.
- Show localized cancellation totals in the reports summary and daily table.
- Include a localized cancellation column and values in reports CSV exports.
- Add deterministic parser, rendering, export, localization, responsive-layout, and regression evidence while preserving existing request and error totals.

## Capabilities

### Modified Capabilities

- `usage-error-metrics`: require the Reports frontend to preserve and visibly surface the cancellation fields already supplied by the reports API.

## Impact

- Affected area: Reports frontend response parsing, date-range zero-fill, summary cards, daily detail table, CSV export, and report translations.
- Compatibility: additive presentation only; existing requests and errors values and CSV semantics remain unchanged apart from the new cancellation column.
- No backend, database, or API contract changes are required.
