## Why

API keys already support enforced model and enforced reasoning effort, and request payloads already normalize the legacy `fast` service-tier alias to `priority`. The dashboard CRUD surface does not yet expose an enforced service tier, so clients cannot persist that policy and alias compatibility breaks at the wrong layer when the field is added.

## What Changes

- Add `enforced_service_tier` to API key persistence and dashboard CRUD schemas.
- Add `omit_priority_request` to API key persistence and dashboard CRUD schemas.
- Normalize `fast` to `priority` in the API key service layer.
- Apply enforced service tier to proxied Responses payloads alongside model and reasoning enforcement.
- When a key omits priority requests, submit priority/fast service-tier requests without `service_tier` while preserving the requested tier in request logs.

## Capabilities

### Modified Capabilities

- `api-keys`
- `responses-api-compat`
