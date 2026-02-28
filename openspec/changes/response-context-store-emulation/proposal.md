# response-context-store-emulation

## Why

Clients like OpenClaw and Home Assistant rely on Responses-style chaining (`store=true`, `previous_response_id`, `item_reference`).
Current behavior rejects `store=true` and `previous_response_id`, causing continuity failures and 404s from upstream when `store=false` is enforced.

## What changes

1. Accept `store=true` and `previous_response_id` at API level.
2. Keep upstream compatibility by forcing upstream payloads to `store=false` and clearing `previous_response_id` after local expansion.
3. Add durable response context storage in database with API-key scoping and TTL cleanup.
4. Expand local reference resolution for both `item_reference` and `previous_response_id` before upstream call.
5. Return a deterministic OpenAI-style 404 `not_found` error when local reference resolution fails.

## Impact

- Better compatibility with OpenAI Responses clients.
- No upstream contract change required.
- Adds new DB tables and migration for response context durability.
