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
declared rather than inferred, and validated by shape rather than against a
fixed enum. That includes `none`, which the #1660 backend survey shows is a
real value (GLM `max`/`high`/`none`), and which is already first-class for
API-key enforced efforts in `app/modules/api_keys/service.py`; filtering it out
of source catalogs alone would have left the two vocabularies disagreeing.

## What Changes

- Read `supported_reasoning_levels`, `default_reasoning_level`, and
  `supports_reasoning_summaries` for source-model catalog entries from
  `raw_metadata_json` instead of hardcoding them.
- Accept both effort slugs (`["low", "high"]`) and objects
  (`[{"effort": "low", "description": "..."}]`), ignoring malformed entries.
- Gate all of it on the existing `"supports_reasoning"` switch, the only
  reasoning control the dashboard exposes, so a model with it off advertises
  nothing and keeps the existing no-reasoning behavior.
- Normalize and deduplicate declared efforts, validating shape rather than
  membership of a fixed vocabulary.
- Undo the unsupported-effort rewrite for requests that are actually routed to
  a model source and that declared the effort, instead of inferring the route
  from registry membership. This covers only the `minimal` workaround; the
  `ultra` -> `max` wire alias mirrors the reference client and stays applied on
  every surface.

## Relationship to `supports_reasoning`

`raw_metadata_json` carries reasoning keys with different jobs:

- `supports_reasoning` is the **switch**. It is written by the dashboard's
  single `Reasoning` checkbox and gates `sanitize_source_chat_payload`, which
  strips `reasoning`, `reasoning_effort` and related toggles on the Chat
  Completions path.
- `supported_reasoning_levels` / `default_reasoning_level` /
  `supports_reasoning_summaries` are the **detail**: which efforts an opted-in
  backend accepts. They are set through the API today; the dashboard UI for
  them is the UI-only rebase of #1675 on this parser.

Detail is gated on the switch. An earlier revision of this change instead made
declared levels imply the switch, which inverted the relationship: it turned a
description of *which* efforts into permission for reasoning at all, and left
the dashboard checkbox reading `false` for a model the backend treated as
opted in. Gating the other way keeps every surface consistent — `/v1/models`
derives `supports_reasoning` from the levels and the summary flag before
consulting the raw key, so a model advertising levels while the sanitizer
strips its chat requests would be visible and inert at once. With the gate, that
state is unreachable.

The Responses path forwards `reasoning` regardless, as it does today: it is a
first-class field of the Responses schema that a source opts into with
`supports_responses`, unlike the chat path where three of the stripped keys are
vendor extensions that only survive because the request model allows extra
fields. Making the Responses path strip as well would reverse that existing
design decision and is out of scope here.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-catalog-compat`
