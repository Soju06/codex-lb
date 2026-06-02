## Why

Issue #371 reports the dashboard usage donuts are hard to read at a glance. Today each donut is titled "5h Remaining" / "Weekly Remaining" and shows a compact-formatted number such as `7.33k` at the center. Operators asked for three concrete clarifications:

- a label that explicitly says "Credits" so the donut is obviously a credit count (rather than tokens, requests, or some other resource),
- raw numbers (`7331`) instead of abbreviations (`7.33k`),
- the remaining count rendered against the total as a fraction (`7331/7560`) so the exact distance to the cap is visible without reading the donut geometry.

## What Changes

- `frontend/src/components/donut-chart.tsx`: add an opt-in `centerLayout: "remaining" | "credits"` prop. Default is `"remaining"` (existing compact-formatted single number with "Remaining" caption — backwards-compatible for any other caller). `"credits"` renders a `Credits` caption above a `{remaining}/{total}` fraction formatted with `formatNumber` (locale-aware thousands separators, no `k`/`M` abbreviation).
- `frontend/src/features/dashboard/components/usage-donuts.tsx`: pass `centerLayout="credits"` for both panels and rename the titles from `"5h Remaining"` and `"Weekly Remaining"` to `"Hourly Credits"` and `"Weekly Credits"` so the title labels read the same as the caption.
- Update `usage-donuts.test.tsx` to assert the new titles, the `Credits` label, and the rendered `{remaining}/{total}` fractions (including the regression case from the issue: `7331/7560`).

## Capabilities

### Modified Capabilities

- `frontend-architecture`: dashboard usage donuts present remaining credit counts as a raw `remaining/total` fraction with the "Credits" caption, instead of a compact-formatted single number under a "Remaining" caption.

## Impact

- **Code**: `frontend/src/components/donut-chart.tsx`, `frontend/src/features/dashboard/components/usage-donuts.tsx`.
- **Tests**: `frontend/src/features/dashboard/components/usage-donuts.test.tsx` (updated assertions for the new title and fraction). The base `donut-chart.test.tsx` still exercises the default `centerLayout="remaining"` path.
- **API/back-end**: no changes. The remaining/total values already arrive from the existing schemas.
- **Operational**: no migration, no settings. The change is contained to the dashboard's usage panel.
