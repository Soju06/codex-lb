## Why

Codex clients now send a terminal `compaction_trigger` item on normal `/backend-api/codex/responses` turns when they want remote compaction. codex-lb currently forwards that item as ordinary Responses input, which leaves the downstream compact flow with no compaction output item and can trap the client in a failed retry loop.

## What Changes

- Detect a terminal top-level `compaction_trigger` item on `/backend-api/codex/responses`.
- Strip the trigger before calling upstream compact handling, then synthesize the raw SSE response Codex expects: one compaction output item followed by terminal completion.
- When a request includes `compaction_trigger`, reject malformed placement where it is duplicated or not the final top-level input item with a 400 OpenAI error.
- Normalize Codex-affinity `/backend-api/codex/responses/compact` responses that include remote-compaction-v2 historical message output so direct compact callers still receive the single compact output item the Codex client expects.
- Keep OpenAI-style `/v1/responses/compact` unchanged.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: Codex responses turns gain explicit compaction-trigger handling, fail-closed validation for malformed trigger placement, and Codex-affinity compact output normalization for remote-compaction-v2 payloads.

## Impact

- `app/modules/proxy/api.py`
- `app/modules/proxy/request_policy.py`
- streaming regression tests for Codex responses
- OpenSpec spec and task artifacts for `responses-api-compat`
