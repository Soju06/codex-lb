## Why

At 320x568 and 390x844 viewports, dashboard usage donut cards resolve to a 475px intrinsic width and widen the document beyond the mobile content area. Operators need the usage cards to remain readable and contained without changing the request table's intentional local horizontal scrolling or the desktop layout.

## What Changes

- Constrain the usage donut grid items and the donut card, row, and legend min-content seams so they can shrink within the mobile dashboard content width.
- Allow the existing account-section heading and summary group to wrap at the smallest viewport, removing the separately identified 4px residual without clipping or global overflow suppression.
- Preserve the fixed-size chart, existing legend truncation and scrolling behavior, and desktop two-column layout.
- Add rendered browser-smoke coverage for document and usage-card containment while proving the request table still scrolls only inside its local scroller.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Require dashboard usage donut cards to remain within supported mobile content widths while preserving local horizontal scrolling for the request table.

## Impact

- Affected dashboard components: `frontend/src/features/dashboard/components/usage-donuts.tsx`, `frontend/src/components/donut-chart.tsx`, and the account-section header in `frontend/src/features/dashboard/components/dashboard-page.tsx`.
- Affected test surface: focused dashboard browser smoke at 320x568, 390x844, and desktop.
- No API, schema, persistence, dependency, configuration, request-table, navigation, or unrelated visual-style changes.
