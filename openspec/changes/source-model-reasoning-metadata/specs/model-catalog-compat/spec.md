## ADDED Requirements

### Requirement: Source-model catalog entries advertise operator-declared reasoning efforts

Codex catalog entries built for OpenAI-compatible source models MUST derive
`supported_reasoning_levels`, `default_reasoning_level`, and
`supports_reasoning_summaries` from the source model's `raw_metadata_json`
rather than reporting a fixed no-reasoning capability.

Derivation MUST be gated on `"supports_reasoning": true`. That flag is the only
reasoning control the dashboard exposes, so a model whose operator left it off
MUST advertise no efforts, no default, and no summary support regardless of what
else the metadata declares. Levels say *which* efforts an opted-in backend
accepts, not *whether* reasoning is permitted, and gating them on the same flag
that gates the chat-completions sanitizer is what keeps the Codex catalog,
`/v1/models` and the dashboard checkbox in agreement.

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

### Requirement: The reasoning switch is the single opt-in across every surface

`"supports_reasoning": true` MUST remain the only reasoning opt-in for a source
model. Declared levels or `supports_reasoning_summaries` MUST NOT imply it.

Because catalog derivation is gated on the same flag, the surfaces cannot
disagree: with the switch off the model advertises no efforts, `/v1/models`
reports `supports_reasoning: false`, the chat-completions sanitizer strips the
client's reasoning fields, and the unsupported-effort restore has no declared
effort to act on. With it on, the operator's declared efforts reach all of them.

#### Scenario: The switch is off

- **GIVEN** a source model that declares `supported_reasoning_levels` and
  `supports_reasoning_summaries` but not `"supports_reasoning": true`
- **WHEN** its catalog entry is built and a chat-completions request for it
  carries reasoning fields
- **THEN** the entry advertises no efforts, no default, and no summary support
- **AND** `/v1/models` reports `supports_reasoning: false`
- **AND** the request's reasoning fields are stripped

#### Scenario: The switch is on

- **GIVEN** the same source model with `"supports_reasoning": true` added
- **WHEN** its catalog entry is built and a chat-completions request for it
  carries reasoning fields
- **THEN** the entry advertises the declared efforts and summary support
- **AND** the request's reasoning fields are forwarded

#### Scenario: The switch alone still opts in

- **GIVEN** a source model that sets only `"supports_reasoning": true`
- **WHEN** a chat-completions request for that model carries reasoning fields
- **THEN** the fields are forwarded, and the entry advertises no specific efforts

### Requirement: The unsupported-effort rewrite is undone for source-routed requests

The `minimal` normalization works around a ChatGPT/Codex backend that drops the
value, hanging the stream. Model sources do not have that defect, so a request
served by one MUST NOT be downgraded by it.

Whether a request is served by a model source is known only after source
selection, which runs after enforcement. The rewrite MUST therefore be applied
unconditionally at enforcement time, and the replaced effort MUST be reported to
the caller so it can be restored once a source has actually been selected.
Restoration MUST occur only when a source was selected and the replaced effort
is among the efforts that source declares for the model. Declared efforts are
read through the same `"supports_reasoning"` gate as the catalog, so a model
whose switch is off has none and is never restored. The reported effort
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
