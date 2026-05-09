## Why

Clients need to send `service_tier: "ultrafast"` as a first-class literal tier. The proxy currently accepts and accounts for `priority`, `flex`, `default`, and `auto`, while `fast` is a legacy alias for `priority`.

## What Changes

- Accept `ultrafast` as a supported service-tier literal for API key enforcement.
- Preserve `ultrafast` in Responses request payloads and request-log tier fields instead of canonicalizing it to another tier.
- Price `ultrafast` requests with the existing priority-tier pricing schedule until a distinct published pricing table exists.
- Expose `ultrafast` in the dashboard API-key service-tier controls.

## Impact

- Affected specs: `responses-api-compat`, `api-keys`, `chat-completions-compat`, `frontend-architecture`
- Affected code: service-tier normalization, API-key schema validation, usage pricing, dashboard selectors
