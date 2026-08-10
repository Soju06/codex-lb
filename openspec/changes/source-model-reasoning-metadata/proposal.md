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
- Skip the `minimal` normalization workaround for models a populated registry
  snapshot does not list, so a declared effort reaches the source unchanged.

## Relationship to `supports_reasoning`

`raw_metadata_json` now carries two independent reasoning keys, and operators
need to know both:

- `supported_reasoning_levels` / `default_reasoning_level` drive **catalog
  advertising**. Codex clients read them from the Codex model catalog to
  populate the reasoning-effort picker.
- `supports_reasoning` gates `sanitize_source_chat_payload`, which strips
  `reasoning`, `reasoning_effort` and the related toggles on the **Chat
  Completions** path only. The Responses path forwards them regardless.

Declaring levels without also setting `supports_reasoning` therefore advertises
efforts that Codex (Responses) honors while chat-completions callers silently
lose them. This change documents the split rather than coupling the two keys,
because the chat-path sanitizer protects backends that reject unknown fields and
inferring that opt-in from an advertising declaration would remove that
protection silently. The dashboard side of this — a single `Reasoning` toggle
that only affects the chat path, and no UI for the levels at all — is tracked
separately in #1672.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-catalog-compat`
