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

Declared efforts MUST be normalized (trimmed and lowercased) and MUST be
restricted to the efforts the client surfaces understand: `minimal`, `low`,
`medium`, `high`, `xhigh`, `max`, `ultra`. An effort outside that set MUST be
dropped. Codex clients deserialize the model catalog as a whole, so an
operator typo must not be able to affect entries other than its own.

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

#### Scenario: Unknown efforts and casing variants are normalized away

- **GIVEN** a source model whose `raw_metadata_json` sets
  `"supported_reasoning_levels": [" Low ", "HIGH", "turbo"]` and
  `"default_reasoning_level": " HIGH "`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises exactly `low` and `high`
- **AND** `default_reasoning_level` is `high`

#### Scenario: Models without reasoning metadata keep the previous behavior

- **GIVEN** a source model with no `raw_metadata_json`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises no reasoning efforts, no default effort, and no
  reasoning-summary support

### Requirement: Declared reasoning efforts imply the chat-path reasoning opt-in

A source model that declares `supported_reasoning_levels` MUST be treated as
having opted into reasoning for the chat-completions path, as if
`"supports_reasoning": true` were set. Advertising efforts on `/v1/models` while
the chat-completions sanitizer strips the client's reasoning fields would make
the same capability simultaneously visible and inert. The explicit
`"supports_reasoning": true` opt-in MUST keep working for models that declare no
levels.

#### Scenario: Declaring levels enables reasoning on the chat path

- **GIVEN** a source model that declares `supported_reasoning_levels` and does
  not set `"supports_reasoning"`
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
an API key overrode.

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
