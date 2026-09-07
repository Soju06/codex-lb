## Why

The dashboard currently exposes weekly credits pace but does not provide a compact view of daily request activity. Operators should be able to switch that dashboard slot to a familiar GitHub-style activity heatmap without adding a second always-visible panel or changing the existing quota calculations.

## What Changes

- Add a local-only Appearance setting that selects either Weekly credits pace or Request activity heatmap.
- Preserve the existing Weekly credits pace rendering when that mode is selected.
- Add a responsive, theme-aware GitHub-style heatmap covering the most recent six months, with date/request-count tooltips and no axis labels.
- Provide daily request counts through an efficient dashboard data path, and verify that the change does not introduce an unindexed table scan.

## Capabilities

### New Capabilities

- `dashboard-request-activity`: Six-month daily request activity heatmap and its display-mode contract.

### Modified Capabilities

- `frontend-architecture`: Dashboard display preferences gain a local-only mode switch for the weekly pace slot.

## Impact

The frontend dashboard, Appearance settings, local dashboard preference store, translations, and related tests will change. The dashboard overview API and repository may gain a grouped daily request-count field if the existing response does not already contain sufficient data. No persisted settings schema, migration, or new dependency is intended.
