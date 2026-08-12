## ADDED Requirements

### Requirement: Source-model catalog entries advertise operator-declared reasoning efforts

Codex catalog entries built for OpenAI-compatible source models MUST derive
`supported_reasoning_levels`, `default_reasoning_level`, and
`supports_reasoning_summaries` from the source model's `raw_metadata_json`
rather than reporting a fixed no-reasoning capability.

`supported_reasoning_levels` MUST accept a list of effort slugs and a list of
`{"effort", "description"}` objects. Entries that are neither a string nor a
mapping with a string `effort`, and duplicate efforts, MUST be ignored. A
non-list value MUST yield no advertised efforts. `default_reasoning_level` MUST
be reported only when it matches one of the advertised efforts. A source model
without reasoning metadata MUST continue to advertise no efforts, no default,
and no summary support.

Declared efforts MUST be normalized (trimmed and lowercased) and
deduplicated. They MUST NOT be filtered against a fixed vocabulary: backends
disagree on which efforts exist -- `none` is real on GLM and Alibaba Model
Studio, while others stop at `low`/`high`/`max` -- so an enum would drop
efforts a provider genuinely accepts. Only shape is validated; an entry that
is not a string, a mapping without a string `effort`, or an empty slug MUST be
dropped.

#### Scenario: Effort slugs are advertised in declaration order

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": ["low", "medium", "high", "xhigh"]` and
  `"default_reasoning_level": "high"`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises efforts `low`, `medium`, `high`, `xhigh` in that order
- **AND** `default_reasoning_level` is `high`

#### Scenario: Effort objects carry operator descriptions and summary support

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": [{"effort": "low", "description": "Low effort"}]`
  and `"supports_reasoning_summaries": true`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the `low` effort is advertised with description `Low effort`
- **AND** `supports_reasoning_summaries` is `true`

#### Scenario: Malformed entries and out-of-range defaults are dropped

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": ["low", "low", {"description": "x"}, 7, {"effort": "high"}]`
  and `"default_reasoning_level": "ultra"`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises exactly `low` and `high`
- **AND** `default_reasoning_level` is absent

#### Scenario: Casing variants are normalized, unknown efforts are kept

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": [" Low ", "HIGH", "provider-specific"]` and
  `"default_reasoning_level": " HIGH "`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises `low`, `high`, and `provider-specific`
- **AND** `default_reasoning_level` is `high`

#### Scenario: An operator-declared `none` survives

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": ["none", "high", "max"]` and
  `"default_reasoning_level": "none"`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises `none`, `high`, and `max`
- **AND** `default_reasoning_level` is `none`

#### Scenario: Models without reasoning metadata keep the previous behavior

- **GIVEN** a source model with no `raw_metadata_json`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises no reasoning efforts, no default effort, and no
  reasoning-summary support

### Requirement: Declared reasoning capabilities imply the chat-path reasoning opt-in

A source model that declares `supported_reasoning_levels` or
`"supports_reasoning_summaries": true` MUST be treated as having opted into
reasoning for the chat-completions path, as if `"supports_reasoning": true` were
set. Both keys are surfaced as `supports_reasoning` on `/v1/models`, so
advertising either while the chat-completions sanitizer strips the client's
reasoning fields would make the same capability simultaneously visible and
inert. The explicit `"supports_reasoning": true` opt-in MUST keep working for
models that declare neither.

A declared capability MUST win over an explicit `"supports_reasoning": false`.
That mirrors the `/v1/models` derivation, which reports `supports_reasoning` from
the declared levels and summary flag before consulting the raw key, so honoring
the veto on the chat path alone would reintroduce the same visible-and-inert
contradiction. An operator whose backend accepts efforts on the Responses path
but rejects them on the chat path must omit the declarations rather than veto
them.

#### Scenario: A declared capability outranks an explicit false

- **GIVEN** a source model that declares `supported_reasoning_levels` and sets
  `"supports_reasoning": false`
- **WHEN** a chat-completions request for that model carries reasoning fields
- **THEN** the fields are forwarded, matching what `/v1/models` advertises

#### Scenario: Declaring levels enables reasoning on the chat path

- **GIVEN** a source model that declares `supported_reasoning_levels` and does
  not set `"supports_reasoning"`
- **WHEN** a chat-completions request for that model carries reasoning fields
- **THEN** the fields are forwarded rather than stripped

#### Scenario: Declaring summary support enables reasoning on the chat path

- **GIVEN** a source model that sets `"supports_reasoning_summaries": true` and
  declares neither `supported_reasoning_levels` nor `"supports_reasoning"`
- **WHEN** a chat-completions request for that model carries reasoning fields
- **THEN** the fields are forwarded rather than stripped

#### Scenario: Models with neither key keep reasoning stripped

- **GIVEN** a source model with no reasoning metadata
- **WHEN** a chat-completions request for that model carries reasoning fields
- **THEN** the fields are stripped as before

### Requirement: The unsupported-effort rewrite is undone for source-routed requests

The `minimal` normalization works around a ChatGPT/Codex backend that drops the
value, hanging the stream. Model sources do not have that defect, so a request
served by one MUST NOT be downgraded by it.

Whether a request is served by a model source is known only after source
selection, which runs after enforcement. The rewrite MUST therefore be applied
unconditionally at enforcement time, and the replaced effort MUST be reported to
the caller so it can be restored once a source has actually been selected.
Restoration MUST occur only when a source was selected and the replaced effort
is among the efforts that source declares for the model. The reported effort
MUST be the post-enforcement value, so restoring it cannot resurrect an effort
an API key overrode, and MUST be the normalized (trimmed, lowercased) form, so
restoration cannot reintroduce a casing variant the normalizer removed.

Restoration MUST apply only to efforts replaced by the unsupported-effort
fallback. The `ultra` -> `max` rewrite is a wire alias rather than a workaround:
it mirrors the reference client and is required on every upstream surface, so it
MUST remain applied to source-routed payloads even when the source declares
`ultra`.

Registry membership MUST NOT be used to decide this. A populated snapshot can
omit a genuine subscription model — a partial refresh, an account unavailable
during refresh, or an operator-mapped slug outside the bootstrap set — and those
requests still reach the ChatGPT backend, where skipping the rewrite restores
the hang. Conversely a source model whose slug shadows a subscription slug is
present in the snapshot yet source-routed.

#### Scenario: A source that declared the effort receives it unchanged

- **GIVEN** a source model declaring `["minimal", "low", "high"]`
- **AND** a request for that model with `reasoning.effort` of `minimal`
- **WHEN** the request is routed to the source
- **THEN** the source receives `minimal`

#### Scenario: A source that did not declare the effort keeps the safe value

- **GIVEN** a source model declaring `["low", "high"]`
- **AND** a request for that model with `reasoning.effort` of `minimal`
- **WHEN** the request is routed to the source
- **THEN** the source receives the rewritten effort

#### Scenario: A source declaring ultra still receives the max alias

- **GIVEN** a source model declaring `["ultra", "max"]`
- **AND** a request for that model with `reasoning.effort` of `ultra`
- **WHEN** the request is routed to the source
- **THEN** the source receives `max`

#### Scenario: Subscription requests keep the workaround

- **GIVEN** a request with `reasoning.effort` of `minimal` that is not routed to
  a model source, including one whose model is absent from a populated registry
  snapshot
- **WHEN** the request is forwarded
- **THEN** the effort is rewritten to the model's lowest supported effort

#### Scenario: WebSocket requests keep the workaround

- **GIVEN** a WebSocket Responses request with `reasoning.effort` of `minimal`
- **WHEN** the request is forwarded
- **THEN** the effort is rewritten, because the WebSocket transport never
  reaches a model source

#### Scenario: An enforced effort is not resurrected by restoration

- **GIVEN** an API key that enforces a reasoning effort
- **AND** a request for a source model that declares the client's original effort
- **WHEN** the request is routed to the source
- **THEN** the source receives the enforced effort
