## Why

Unknown dashboard URLs render an empty route outlet, while a rejected lazy page
chunk escapes Suspense and unmounts the React root. Both failures leave
operators without context or recovery.

## What Changes

- Preserve the authenticated shell and render Not Found navigation for unknown
  routes.
- Show a visible pending state for lazy routes.
- Contain lazy-import/render failures inside main content with reload and
  Dashboard actions.
- Provide deterministic focus, keyboard operation, and localized copy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: dashboard routing becomes recoverable without
  weakening route-level code splitting.

## Impact

- SPA route tree, one recovery component, localized copy, and route tests.
- No API, schema, dependency, backend, or navigation-budget change.
