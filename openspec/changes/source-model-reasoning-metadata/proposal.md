## Why

Source-model Codex catalog entries hardcode `supported_reasoning_levels=()`,
`default_reasoning_level=None`, and `supports_reasoning_summaries=False`. Every
other client-capability field on those entries is an operator-overridable
`raw_metadata_json` default, so a reasoning-capable backend has no way to
advertise its efforts and Codex clients show no reasoning-effort options for
model-source models.

The efforts themselves already reach the source: the Responses path forwards
`reasoning` unchanged, so an operator who hardcodes `model_reasoning_effort` in
`config.toml` gets working reasoning today. Only the advertisement is missing,
which makes the capability undiscoverable in the client UI.

Backends differ in the efforts they accept — for example Alibaba Model Studio
exposes `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`, while DeepSeek and
Kimi expose `low`/`high`/`max` — so the advertised set has to be operator
declared rather than inferred.

## What Changes

- Read `supported_reasoning_levels`, `default_reasoning_level`, and
  `supports_reasoning_summaries` for source-model catalog entries from
  `raw_metadata_json` instead of hardcoding them.
- Accept both effort slugs (`["low", "high"]`) and objects
  (`[{"effort": "low", "description": "..."}]`), ignoring malformed entries.
- Keep the existing no-reasoning behavior for models that do not opt in.
- Normalize declared efforts and clamp them to the set the client surfaces
  understand, so one operator typo cannot affect other catalog entries.
- Undo the `minimal` normalization for requests that are actually routed to a
  model source and that declared the effort, instead of inferring the route
  from registry membership.

## Relationship to `supports_reasoning`

`raw_metadata_json` carries two reasoning keys with different jobs:

- `supported_reasoning_levels` / `default_reasoning_level` drive **catalog
  advertising**. Codex clients read them to populate the reasoning-effort picker.
- `supports_reasoning` gates `sanitize_source_chat_payload`, which strips
  `reasoning`, `reasoning_effort` and related toggles on the **Chat
  Completions** path only. The Responses path forwards them regardless.

Declaring levels now implies the chat-path opt-in. The earlier revision of this
change documented the split instead, but that left `/v1/models` reporting
`supports_reasoning: true` (it is derived from a non-empty level set) for a
model whose chat requests are silently stripped — a contradiction visible on the
API surface, not merely a documentation subtlety. The explicit
`"supports_reasoning": true` opt-in still works on its own for models that
declare no levels, so the sanitizer keeps protecting backends that reject
unknown fields.

The dashboard side of this — a single `Reasoning` toggle that only affects the
chat path, and no UI for the levels at all — is tracked separately in #1672.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-catalog-compat`
