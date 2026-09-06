## Why

After the first dashboard overview request reaches terminal failure, the page
shows the endpoint message but keeps its page-wide skeleton. The operator gets
neither an announced terminal state nor an in-page recovery action.

## What Changes

- Distinguish pending initial load from terminal no-data failure.
- Replace the terminal skeleton with an announced error and keyboard Retry.
- Keep that error visible while Retry is in flight and recover in place.
- Preserve shell, query key/retry policy, and cached-overview behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: terminal dashboard overview failures become
  actionable and announced.

## Impact

- Dashboard page state and one App-route integration regression.
- No API, schema, query key, locale, dependency, backend, or navigation change.
