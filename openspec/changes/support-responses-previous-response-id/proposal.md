## Why
The original `previous_response_id` work in `#211` mixed durable continuity improvements with an unwanted PostgreSQL-first backend rewrite. `codex-lb` still needs the continuity gains, but SQLite remains the default runtime and must stay first-class. We need to restore the remaining wins on the existing project-native database primitives.

## What Changes
- Persist terminal Responses snapshots in the default database so `previous_response_id` can survive process restart and HTTP bridge loss.
- Resolve `previous_response_id` from caller-scoped continuity state when live upstream continuity is unavailable, while preserving the conflict validation against `conversation`.
- Prefer the originating upstream account for replay when that account is still eligible.
- Retry one websocket request on early upstream disconnect before `response.created`.
- Extend migration, HTTP bridge, websocket, and API-key scoping regression coverage and sync the specs.

## Impact
- Restores durable `previous_response_id` compatibility for newer Codex CLI and OpenAI-style Responses flows without making PostgreSQL mandatory.
- Preserves SQLite as the default runtime while keeping PostgreSQL optional.
- Improves resilience for native websocket clients during early upstream disconnects.
