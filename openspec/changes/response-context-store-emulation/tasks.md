# Tasks

- [x] Add settings for response context TTL/capacity/durable switch.
- [x] Add durable response context schema + alembic migration.
- [x] Add response context repository with scoped resolve + cleanup.
- [x] Add configurable global fallback toggle for response context scope resolution.
- [x] Allow `store=true` and `previous_response_id` in request validators.
- [x] Expand chaining references before upstream calls and map unresolved refs to 404.
- [x] Persist completed/incomplete responses to runtime cache and durable store (when requested).
- [x] Add background cleanup scheduler for durable response context TTL purge.
- [x] Add integration tests for store acceptance, previous_response_id chaining, unresolved ref 404.
- [x] Add repository tests for scoping and expiration cleanup.
