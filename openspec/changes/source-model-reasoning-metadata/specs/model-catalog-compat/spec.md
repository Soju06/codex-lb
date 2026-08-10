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

#### Scenario: Models without reasoning metadata keep the previous behavior

- **GIVEN** a source model with no `raw_metadata_json`
- **WHEN** a client fetches the Codex model catalog
- **THEN** the entry advertises no reasoning efforts, no default effort, and no
  reasoning-summary support

### Requirement: Declared efforts survive the unsupported-effort normalization

The `minimal` normalization exists to work around a ChatGPT/Codex backend that
drops the value, and MUST NOT be applied to models that backend does not serve.
When a populated model-registry snapshot has no entry for the requested model,
the requested reasoning effort MUST be forwarded unchanged. When no snapshot is
available the existing conservative rewrite MUST still apply, because the
request cannot then be attributed to a model source.

#### Scenario: A source model keeps a declared minimal effort

- **GIVEN** a populated registry snapshot that lists only subscription models
- **AND** a request for a model absent from that snapshot with
  `reasoning.effort` of `minimal`
- **WHEN** the unsupported-effort normalization runs
- **THEN** the effort remains `minimal`

#### Scenario: Subscription models keep the workaround

- **GIVEN** a populated registry snapshot that lists the requested model
- **AND** a request with `reasoning.effort` of `minimal`
- **WHEN** the unsupported-effort normalization runs
- **THEN** the effort is rewritten to the model's lowest supported effort

#### Scenario: An unavailable snapshot keeps the conservative rewrite

- **GIVEN** no registry snapshot is available
- **AND** a request with `reasoning.effort` of `minimal`
- **WHEN** the unsupported-effort normalization runs
- **THEN** the effort is rewritten to the default fallback
