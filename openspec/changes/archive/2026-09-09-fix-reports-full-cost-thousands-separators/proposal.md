## Why

The `/reports` page has a shared USD formatter that renders full currency values with grouping separators, but several non-compact Cost surfaces bypass it with manual `toFixed(2)` string interpolation. Operators therefore see `$1400.00` instead of `$1,400.00` in the affected full-value surfaces.

## What Changes

- Use the existing shared USD formatter for non-compact Reports Cost values in the summary card, average-cost subtitle, Daily Breakdown table, Cost by Day chart axis, and chart tooltip.
- Preserve intentionally compact Cost labels such as `$1.4K` in constrained distribution-chart surfaces.
- Preserve full decimal precision in Daily Breakdown CSV export; its machine-readable output is not a display surface.

## Capabilities

### New Capabilities

### Modified Capabilities

- `frontend-architecture`: `/reports` full-value USD display surfaces use grouped currency formatting while compact visualizations retain compact notation.

## Impact

- Frontend: Reports summary cards, Daily Breakdown table, and Cost by Day chart tooltip.
- Tests: focused Reports component regression coverage for grouped full-value currency rendering.
- Specs: `frontend-architecture` delta for Reports currency presentation.
