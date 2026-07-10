## Why

Codex 0.144 ships GPT-5.6 family model catalog entries (`gpt-5.6-sol`,
`gpt-5.6-terra`, and `gpt-5.6-luna`) with extended reasoning efforts. When
codex-lb starts before a successful upstream model refresh, its bundled
bootstrap catalog still lacks those slugs and dashboard/API-key validation still
rejects the new `max` and `ultra` reasoning effort values.

## What Changes

- Add GPT-5.6 Sol, Terra, and Luna to the static bootstrap model catalog with
  Codex-compatible context window, speed-tier, websocket, and reasoning
  metadata.
- Allow `max` and `ultra` reasoning efforts where codex-lb validates model
  reasoning choices for dashboard model metadata, automations, and API-key
  reasoning enforcement.
- Keep refreshed upstream model registry data authoritative over the bootstrap
  catalog.

## Impact

- No database migration.
- Offline/startup model listing includes GPT-5.6 family models.
- Dashboard and API-key forms can preserve and submit the extended reasoning
  efforts advertised by the model catalog.
