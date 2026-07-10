## Why

Codex 0.144 ships GPT-5.6 family model catalog entries (`gpt-5.6-sol`,
`gpt-5.6-terra`, and `gpt-5.6-luna`) with extended reasoning efforts. When
codex-lb starts before a successful upstream model refresh, its bundled
bootstrap catalog still lacks those slugs and dashboard/API-key validation still
rejects the new `max` and `ultra` reasoning effort values. A bootstrap-served
Codex catalog must also match the upstream bundled metadata exactly
(`codex-rs/models-manager/models.json` at rust-v0.144.1): missing fields such
as `tool_mode`, `multi_agent_version`, or the deserializer-required
`experimental_supported_tools` change Codex client behavior or make the entry
unparseable client-side.

## What Changes

- Add GPT-5.6 Sol, Terra, and Luna to the static bootstrap model catalog with
  metadata that mirrors the upstream bundled catalog field-for-field
  (context window 372000, `minimal_client_version` 0.144.0,
  `tool_mode: code_mode_only`, per-model `multi_agent_version`,
  `use_responses_lite`, reasoning-summary metadata, 21-plan availability,
  Sol's `availability_nux`, speed/service tiers, websocket preference). The
  large `base_instructions` / `model_messages` prompts are deliberately left
  to the live refresh.
- Allow `max` and `ultra` reasoning efforts where codex-lb validates model
  reasoning choices for dashboard model metadata, automations, and API-key
  reasoning enforcement, and accept them in the OpenAI-compatible `thinking`
  string alias.
- Alias `ultra` to `max` on the upstream wire (requested or enforced), exactly
  like the reference Codex client (`reasoning_effort_for_request`, codex-rs
  `core/src/client.rs` at rust-v0.144.1); `ultra` itself is never forwarded.
  Automation compact pings, which build upstream payloads directly and bypass
  the proxy request-policy rewrite, apply the same aliasing.
- Recognize Cursor-style suffixed labels for the GPT-5.6 personality slugs
  (e.g. `gpt-5.6-sol-extra-high-fast` normalizes to `gpt-5.6-sol` plus derived
  reasoning/service-tier fields) in the model alias normalizer; `ultra`/`max`
  stay outside the suffix grammar because not every GPT-5-family base supports
  them.
- Raise the fallback Codex client version (`model_registry_client_version`)
  from 0.101.0 to 0.144.0 so a degraded-startup registry refresh still
  receives GPT-5.6 from upstream.
- Keep refreshed upstream model registry data authoritative over the bootstrap
  catalog.

## Impact

- No database migration.
- Offline/startup model listing includes GPT-5.6 family models with
  upstream-exact metadata.
- Dashboard and API-key forms can preserve and submit the extended reasoning
  efforts advertised by the model catalog; upstream never sees the
  client-plane `ultra` literal.
