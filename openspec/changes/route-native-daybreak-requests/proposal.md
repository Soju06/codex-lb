## Why

Clients should use one endpoint and one API key for ordinary and Daybreak work. Daybreak requests already identify their intent through the legacy model or native access-program field; a custom capability header should not be required. This implements Soju06/codex-lb#2703.

## What Changes

Add a per-API-key automatic Daybreak routing toggle, disabled for existing and new keys until enabled. When enabled, recognize the legacy `gpt-daybreak-blue-latest` model and explicit native `access_programs.cyber = "daybreak_blue"`. Reuse existing Trusted Cyber eligibility, API-key scope, lineage, retry and ownership constraints. Forward native fields unchanged. Validate malformed/conflicting/unsupported native selectors, protect safe replay, and apply the same mandatory eligible-pool constraint to HTTP Responses and compact requests. Preserve existing custom-carrier behavior when the toggle is off.

## Capabilities

### Modified Capabilities
- `api-keys`: persisted per-key toggle, dashboard create/edit and cache invalidation.
- `responses-api-compat`: automatic Daybreak intent, forwarding and unsupported transport denial.
- `account-routing`: automatically detected intent narrows the permitted account pool.

## Impact

One Boolean column and migration, API-key lifecycle/UI, request intent parsing, Responses WebSocket and direct HTTP/compact preparation, replay validation, tests and docs. Based on current upstream main, with the migration following its existing head. Actual client delivery of native selectors remains a separate client compatibility concern (#2580). Daybreak Red support is not inferred from the existing Blue eligibility toggle.

## HTTP transport decision

Required HTTP Responses and compact requests bypass external model sources and the HTTP session bridge, using the existing direct subscription transport with a mandatory eligibility constraint. Ordinary traffic retains its transport policy. Account ownership and key scope still apply. Required retry exhaustion never enables ordinary selection.
