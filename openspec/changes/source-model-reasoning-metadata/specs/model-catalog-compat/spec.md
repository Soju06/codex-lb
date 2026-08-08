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
