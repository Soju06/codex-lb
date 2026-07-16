# model-catalog-compat delta: fix-codex-catalog-required-fields

## ADDED Requirements

### Requirement: Every Codex-native catalog entry is wire-parseable

Every model entry returned by `GET /backend-api/codex/models` or the equivalent
`GET /v1/models?client_version=<version>` route MUST include the non-defaulted
Codex wire fields `truncation_policy` and `experimental_supported_tools`, even
when the entry comes from hidden retained bootstrap metadata or a persisted
legacy registry snapshot. When either field is absent from stored raw metadata,
the mapper MUST provide a conservative model-compatible default. Wire-valid
values provided by a live upstream catalog or model source MUST remain
authoritative and MUST NOT be overwritten by the compatibility defaults. When
`experimental_supported_tools` is not a list, the mapper MUST emit an empty
list. When it contains non-string members, the mapper MUST omit those members
rather than failing the complete catalog. A wire-valid `truncation_policy` MUST
use the `bytes` or `tokens` mode and a JSON integer representable by Codex's
signed 64-bit `limit` field. When an explicit policy does not satisfy that wire
shape, the mapper MUST emit the same conservative model-compatible policy used
when the field is absent.

#### Scenario: Hidden bootstrap metadata cannot invalidate the live catalog

- **GIVEN** a successful live refresh omits an older bundled model
- **AND** codex-lb retains that model as hidden metadata whose raw payload lacks
  required Codex wire fields
- **WHEN** a Codex client requests the native model catalog
- **THEN** the hidden entry includes a valid `truncation_policy`
- **AND** it includes `experimental_supported_tools` as a list
- **AND** the complete catalog can be deserialized instead of falling back to
  bundled client metadata

#### Scenario: Explicit valid upstream compatibility values win

- **GIVEN** a live catalog or model source provides `truncation_policy` or
  `experimental_supported_tools`
- **WHEN** codex-lb renders the Codex-native catalog entry
- **THEN** it preserves those explicit values unchanged

#### Scenario: Invalid source tool members cannot fail the catalog

- **GIVEN** a model source provides `experimental_supported_tools` with both
  string and non-string members
- **WHEN** codex-lb renders the Codex-native catalog entry
- **THEN** it retains the string tool names
- **AND** it omits non-string members instead of returning a server error

#### Scenario: Non-list source tool metadata cannot fail the catalog

- **GIVEN** a model source provides a non-list value for
  `experimental_supported_tools`
- **WHEN** codex-lb renders the Codex-native catalog entry
- **THEN** it emits an empty list instead of returning a server error

#### Scenario: Malformed source truncation policy cannot fail the catalog

- **GIVEN** a model source provides an invalid `truncation_policy`, such as a
  null, non-object, incomplete object, unknown mode, non-integer limit, or
  out-of-range limit
- **WHEN** codex-lb renders the Codex-native catalog entry
- **THEN** it emits the conservative model-compatible truncation policy
- **AND** it does not return a server error

#### Scenario: Client-version alias has the same complete contract

- **WHEN** Codex requests `GET /v1/models` with a non-empty `client_version`
- **THEN** every returned `models` entry satisfies the same required-field
  contract as `GET /backend-api/codex/models`
