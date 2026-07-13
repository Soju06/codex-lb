## Why

OpenAI-compatible model discovery clients need to know when Codex models expose upstream speed tiers, such as GPT-5.5 Fast. The Codex-native `/backend-api/codex/models` endpoint already preserves these upstream fields, but `/v1/models` drops them from `metadata`.

## What Changes

- Preserve upstream speed-tier metadata on `/v1/models` metadata entries.
- Include `additional_speed_tiers`, `service_tiers`, and `default_service_tier` when upstream provides them.
- Keep existing model IDs and pricing/request behavior unchanged.

## Impact

- OpenAI-compatible clients can synthesize fast-mode model aliases from `/v1/models` metadata.
- No database migration or dashboard UI change.
