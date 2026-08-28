## Why

All six dashboard pages were statically imported by `App.tsx`, so the entry chunk carried every page's code (807 KB raw / 214 KB gzip after the chart split); an operator opening `/dashboard` parsed and executed the settings, reports, accounts, automations, and APIs pages too.

## What Changes

- Each route's page component loads via `React.lazy` behind one `<Suspense>` in the app layout; the entry chunk drops to 360 KB raw / 112 KB gzip, with per-page chunks (dashboard 71 KB, settings 133 KB, accounts 52 KB, automations 54 KB, ...) fetched on first visit.
- No visual change: the suspense fallback is null and page data loading already gates rendering.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`: dashboard routes MUST be code-split so the entry chunk excludes unvisited pages' code.

## Impact

`frontend/src/App.tsx` only. Verified on the built output: no page chunk is statically imported or modulepreloaded by the entry; 841 frontend tests green.
