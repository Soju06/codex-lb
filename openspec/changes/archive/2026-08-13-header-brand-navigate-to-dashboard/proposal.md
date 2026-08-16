## Why

The app header brand area (logo + "Codex LB" text) was a plain `<div>` with no
interaction. Operators already on a sub-page had to use the nav pills to return
to the dashboard; a click on the brand area — a common web convention — did
nothing.

## What Changes

- The header brand area becomes a `<Link to="/dashboard">` so clicking the logo
  or "Codex LB" text navigates back to the dashboard.
- Visual presentation is unchanged: the same logo, gradient background, and
  text styling are preserved inside the link wrapper.
- Keyboard accessibility and focus-ring behavior follow existing project link
  conventions via `focus-visible:ring-2 focus-visible:ring-ring`.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: the app header brand area SHALL be a clickable link
  navigating to `/dashboard`.

## Impact

`frontend/src/components/layout/app-header.tsx` only. No API, database, proxy,
or test changes. Existing header tests that rely on the brand area being a plain
`<div>` may need their selectors updated.
