## Why

The ChatGPT-backed Codex WebSocket now emits stale-anchor failures as `invalid_request_error` with no `code` or `param` and the message ``Invalid `previous_response_id`.``. codex-lb does not recognize that observed shape, so it relays the raw 400 instead of entering its existing safe replay or sanitized client-recovery path.

Production evidence on current upstream `main` recorded three affected Codex sessions in one overnight window. In every case the rejected anchor was a successful response from the same session and account only 9–17 seconds earlier, making this an active compatibility gap rather than an old retained response or account-routing mismatch.

## What Changes

- Classify the exact observed parameterless `invalid_request_error` message as a previous-response continuity miss.
- Reuse the existing WebSocket recovery contract: transparently replay self-contained full resends without the anchor, surface sanitized canonical `previous_response_not_found` to Codex-native delta clients, and retain generic masking for public `/v1` clients.
- Preserve classification boundaries for unrelated invalid-request errors and errors naming a different parameter.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Recognize the parameterless invalid-previous-response error shape emitted by the upstream Codex WebSocket and route it through existing stale-anchor recovery and masking.

## Impact

- Shared OpenAI error classification in `app/core/errors.py`.
- Direct Responses WebSocket behavior on `/backend-api/codex/responses` and `/v1/responses` through their existing recovery policies.
- Route-level and classifier regression coverage; no API, schema, migration, dependency, configuration, or dashboard changes.
