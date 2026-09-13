## Why

`POST /backend-api/codex/responses` preserves `stream: false` and returns an
accepted background Response whose status is `queued` or `in_progress`. Stream
settlement nevertheless expects a terminal SSE event and records
`stream_incomplete`, penalizing a healthy account after a successful HTTP JSON
exchange.

## What Changes

- Treat a canonical queued/in-progress background acknowledgement from
  `stream: false` as transport-complete for settlement.
- Record successful request-log and account-health outcomes.
- Preserve nonterminal EOF failure for actual streaming requests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: accepted background JSON completes its HTTP transport
  without making the same lifecycle event terminal for SSE.

## Impact

- HTTP Responses settlement classification only.
- No setting, schema, polling, response shape, or global event classification.
