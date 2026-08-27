## Why

Client-side navigation currently carries the source page's vertical scroll offset into a different dashboard route, so a destination can open with its heading above the viewport. Full-page navigation starts at the top, and in-app top-level navigation should provide the same predictable entry point without breaking history or hash restoration.

## What Changes

- Reset window scroll to the top when an in-app `PUSH` or `REPLACE` navigation changes the pathname and has no hash.
- Preserve browser restoration for `POP` navigation, the current position for same-path query/filter changes, and existing hash-target scrolling for Settings and the legacy Firewall route.
- Add focused route integration coverage plus a real-browser desktop/mobile navigation proof.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Define scroll restoration behavior for dashboard route transitions, history traversal, query-only updates, and hash destinations.

## Impact

- Frontend route shell in `frontend/src/App.tsx`.
- Focused frontend integration and browser-smoke coverage.
- No API, persistence, configuration, navigation-link, or dependency changes.
