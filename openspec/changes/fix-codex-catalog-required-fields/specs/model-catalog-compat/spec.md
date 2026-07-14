# model-catalog-compat delta: fix-codex-catalog-required-fields

## ADDED Requirements

### Requirement: Every Codex-native catalog entry is wire-parseable

Every model entry returned by `GET /backend-api/codex/models` or the equivalent
`GET /v1/models?client_version=<version>` route MUST include the non-defaulted
Codex wire fields `truncation_policy` and `experimental_supported_tools`, even
when the entry comes from hidden retained bootstrap metadata or a persisted
legacy registry snapshot. When either field is absent from stored raw metadata,
the mapper MUST provide a conservative model-compatible default. Values
provided by a live upstream catalog or model source MUST remain authoritative
and MUST NOT be overwritten by the compatibility defaults.

#### Scenario: Hidden bootstrap metadata cannot invalidate the live catalog

- **GIVEN** a successful live refresh omits an older bundled model
- **AND** codex-lb retains that model as hidden metadata whose raw payload lacks
  required Codex wire fields
- **WHEN** a Codex client requests the native model catalog
- **THEN** the hidden entry includes a valid `truncation_policy`
- **AND** it includes `experimental_supported_tools` as a list
- **AND** the complete catalog can be deserialized instead of falling back to
  bundled client metadata

#### Scenario: Explicit upstream compatibility values win

- **GIVEN** a live catalog or model source provides `truncation_policy` or
  `experimental_supported_tools`
- **WHEN** codex-lb renders the Codex-native catalog entry
- **THEN** it preserves those explicit values unchanged

#### Scenario: Client-version alias has the same complete contract

- **WHEN** Codex requests `GET /v1/models` with a non-empty `client_version`
- **THEN** every returned `models` entry satisfies the same required-field
  contract as `GET /backend-api/codex/models`
