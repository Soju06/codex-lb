## Why

The dashboard request log contains many fields, but operators cannot currently
prioritize the fields they use or allocate more horizontal space to values that
need it. Configurable visibility and per-column sizing make the existing table
usable across different workflows and screen sizes.

## What Changes

- Add a column chooser to the existing dashboard Request Logs section.
- Allow each visible request-log column to be resized by dragging its header
  separator, with keyboard adjustment for accessibility.
- Persist visible columns and individual widths in browser-local storage.
- Preserve at least one visible column and provide a control that restores the
  default column layout.
- Derive the table's minimum width from its visible columns so wide layouts
  remain horizontally scrollable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Define configurable visibility, resizable headers,
  persistence, reset behavior, and accessibility for dashboard request logs.

## Impact

- Dashboard-only frontend changes under `frontend/src/features/dashboard/`.
- A small browser-local preference module; no API, database, authentication,
  routing, or deployment changes.
- Focused component, preference, and dashboard integration tests.
