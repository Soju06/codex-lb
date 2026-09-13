## Why

Request logs persist the upstream route mode, pool, endpoint, fallback use,
and fail-closed reason, but the request-log API read model drops every field.
The dashboard therefore cannot explain which configured route served or
blocked a request even though the diagnostic data already exists.

## What Changes

- Preserve credential-safe upstream routing metadata through the request-log
  API response and frontend parser.
- Present the metadata in the existing request details dialog.
- Add API and frontend contract regressions for the five fields.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-proxy-routing`: Persisted route metadata is exposed through the
  operator request-log surface without proxy credentials.

## Impact

Request-log response schemas/mapping, dashboard parsing and request details,
focused API/frontend tests, and localized labels. Database persistence and
proxy routing behavior do not change.
