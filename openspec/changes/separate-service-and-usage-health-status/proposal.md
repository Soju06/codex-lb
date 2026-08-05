## Why

The dashboard status bar presents recent usage synchronization as a generic
green live signal, so operators cannot tell whether the service itself is
ready. The application already exposes infrastructure readiness separately
from upstream health, but the frontend does not surface it.

## What Changes

- Show independent `Service ready` and `Usage synced` signals in the fixed
  dashboard status bar.
- Source service readiness from the existing `/health/ready` endpoint.
- Keep usage freshness derived from the dashboard overview's `lastSyncAt`
  value and its existing 60-second threshold.
- Keep upstream account and provider health outside the service-readiness
  signal.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: require the fixed status bar to distinguish service
  readiness from usage synchronization freshness.

## Impact

- Frontend status-bar rendering, health response parsing, translations, and
  focused component tests.
- No backend API, readiness logic, upstream health logic, database,
  configuration, dependency, or navigation changes.
