## Why

The Reports page currently always renders all five line charts. Operators need
to control which line-chart cards are visible and retain that preference in
browser-local storage without changing the report data request.

## What Changes

- Add a persisted browser-local line-chart visibility preference for Reports.
- Add a multi-select immediately left of the existing date-range controls,
  using the existing localized chart headers as its options.
- Render only the selected line-chart cards while keeping the combined
  `/api/reports` request unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`

## Impact

There are no API, backend, schema, dependency, summary, donut, or table
changes. The summary, donut, and table sections remain unchanged, and the
existing combined `/api/reports` request continues to provide the complete
report payload.
