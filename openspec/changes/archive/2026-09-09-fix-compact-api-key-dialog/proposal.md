## Why

At a 320x568 viewport, the API key creation dialog is taller than the viewport and remains vertically centered, placing its title and Close control above the visible area and its Create action below it. Operators need the complete form to remain usable on compact dashboard viewports without changing desktop behavior or dismissal semantics.

## What Changes

- Constrain the API key creation dialog shell to the viewport while keeping its header and footer visible.
- Replace the two independently scrolling stacked columns with one internal form scroller on compact viewports while preserving the desktop two-column layout.
- Add focused regression coverage for the viewport-bounded shell, single scroll region, and persistent actions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Require the API key creation dialog to keep its title, Close control, and Create action inside compact viewports while making every field reachable through one internal scroll region.

## Impact

- Affected dashboard component: `frontend/src/features/api-keys/components/api-key-create-dialog.tsx`.
- Affected tests: focused API key creation dialog component and browser-smoke tests.
- No API, schema, persistence, dependency, configuration, or API key edit-dialog changes.
