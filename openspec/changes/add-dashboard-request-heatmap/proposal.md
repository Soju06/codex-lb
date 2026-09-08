## Why

The dashboard currently exposes weekly credits pace but does not provide a compact view of daily request activity. Operators should be able to switch that dashboard slot to a familiar GitHub-style activity heatmap without adding a second always-visible panel or changing the existing quota calculations.

## What Changes

- Add a local-only Appearance setting that selects either Weekly credits pace or Request activity heatmap.
- Preserve the existing Weekly credits pace rendering when that mode is selected.
- Add a responsive, theme-aware GitHub-style heatmap covering the latest six calendar months including the current month, using the requested browser IANA timezone, with date/request-count tooltips and no axis labels.
- Provide sparse daily request counts through a bounded dashboard data path that falls back to UTC for absent or invalid timezones, converts local-day bounds correctly across DST, and does not introduce an unindexed table scan.

## Capabilities

### New Capabilities

- `dashboard-request-activity`: Six-month daily request activity heatmap and its display-mode contract.

### Modified Capabilities

- `frontend-architecture`: Dashboard display preferences gain a local-only mode switch for the weekly pace slot.

## Impact

The frontend dashboard, Appearance settings, local dashboard preference store, dedicated activity endpoint, translations, and related tests will change. The folded activity query will obtain its watermark and SQL-grouped totals in one `AccountUsageRollupState LEFT JOIN` rollup statement; the existing bounded raw-tail query will remain separate and be merged by small per-day totals. No persisted settings schema, migration, or new dependency is intended.
