## Why

`codex-lb` currently rejects `previous_response_id` outright even though OpenAI-style Responses clients use it for multi-turn conversation state. That makes `/v1/responses` incompatible with clients that continue a conversation by referencing the last response id instead of resending the full transcript.

The proxy cannot delegate this feature to the ChatGPT upstream because upstream does not accept `previous_response_id`. To close the compatibility gap, the proxy must persist enough local conversation state to rebuild history after restart and replay it upstream as explicit input items.

## What Changes

- persist response-chain snapshots keyed by `response_id` so `previous_response_id` survives restarts
- resolve `previous_response_id` into replayable input history before forwarding upstream
- prefer the account that served the referenced response when it remains eligible, while falling back to normal routing if it does not
- support the same behavior for HTTP streaming, HTTP collected responses, and WebSocket Responses traffic
- add migration and regression coverage for snapshot persistence, chain resolution, and invalid `previous_response_id` failures

## Capabilities

### New Capabilities

- `database-migrations`: durable response-chain snapshots for `previous_response_id` replay

### Modified Capabilities

- `responses-api-compat`: `/v1/responses` and WebSocket Responses requests may continue prior proxy-managed conversations via `previous_response_id`
- `responses-api-compat`: `previous_response_id` resolution prefers the prior account but falls back to normal routing when necessary

## Impact

- Code: proxy request normalization, stream/websocket response settlement, load balancer account selection, new response snapshot repository/service, Alembic migration
- Tests: Responses compatibility integration coverage, proxy routing/unit coverage, migration coverage
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/database-migrations/spec.md`
