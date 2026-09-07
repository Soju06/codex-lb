## Why

API key users need a self-service view of their own usage without receiving the operator dashboard password or access to the administrative dashboard. The existing `/apis` page is administrator-facing and cannot safely satisfy that need because it depends on the dashboard session and exposes account and API key management data.

## What Changes

- Add a standalone `/key-dashboard` SPA route outside the dashboard password/session gate.
- Prompt the user for their API key and authenticate all data requests with that key as a Bearer credential.
- Show lifetime request, token, cached-token, and cost totals for the authenticated key.
- Add a paginated recent-request grid scoped exclusively to the authenticated key.
- Define a narrow request-log response that omits account identity, account plan, API key identity, client identity, conversation identity, source identity, proxy-route identity, and free-form failure details.
- Keep the raw API key in tab memory only and never place it in URLs or durable browser storage.

## Capabilities

### New Capabilities

- `api-key-dashboard`: Self-service API key authentication, usage summary, and privacy-safe recent request logs.

### Modified Capabilities

None.

## Impact

- New backend key-dashboard API module and dependency wiring.
- SPA route/auth boundary changes and a new lazy-loaded frontend feature.
- New frontend localization keys, Zod contracts, data client, and integration coverage.
- Backend integration coverage for strict key authentication, ownership scoping, redaction, ordering, and pagination.
- No database migration or new runtime setting.
