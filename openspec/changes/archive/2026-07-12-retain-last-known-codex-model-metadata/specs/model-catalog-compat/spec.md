## ADDED Requirements

### Requirement: Codex metadata survives a partial live catalog refresh

The proxy MUST retain the last successfully fetched complete metadata for a
bundled Codex model when a later successful live catalog refresh omits that
model. A retained model that is absent from the current live availability snapshot MUST be
returned through the Codex model catalog with hidden visibility so an explicitly
configured client can resolve its metadata without advertising it in the model
picker.

Retained metadata MUST NOT add the model to current plan, account, service-tier,
routing, dashboard, warmup, or `/v1/models` availability. A current live entry
MUST replace the retained entry when the model appears again.
Models outside the bundled Codex catalog MUST NOT be retained after they leave
the current live availability snapshot.

OpenAI-compatible source entries that share a slug with retained Codex metadata
MUST replace retained metadata only when the source entry's effective Codex
visibility is `list` for the requesting client. A same-slug source entry hidden
by raw catalog visibility or by the API key's exact source allowlist MUST NOT
shadow the retained metadata.

#### Scenario: Sol metadata remains resolvable after a partial refresh

- **GIVEN** a successful live catalog refresh returned complete metadata for `gpt-5.6-sol`
- **WHEN** a later successful refresh omits `gpt-5.6-sol`
- **THEN** the Codex catalog includes the last complete Sol metadata with hidden visibility
- **AND** `/v1/models` and live availability indexes omit Sol

#### Scenario: A later live entry replaces retained metadata

- **GIVEN** metadata was retained for a model omitted by a previous refresh
- **WHEN** a later live refresh returns that model with updated metadata
- **THEN** the updated live metadata is used and the model follows its current live visibility

#### Scenario: Hidden source entry does not replace retained metadata

- **GIVEN** metadata was retained for `gpt-5.6-sol`
- **AND** an OpenAI-compatible source exposes the same `gpt-5.6-sol` slug
- **AND** that source entry is hidden from the effective Codex catalog by raw visibility or an API key's exact source allowlist
- **WHEN** a client calls `GET /backend-api/codex/models` with that API key
- **THEN** the hidden Sol catalog entry uses the retained Codex metadata

#### Scenario: Visible same-slug source follows an earlier hidden source

- **GIVEN** multiple enabled sources expose the same model slug
- **AND** an earlier source is hidden while a later source is list-visible
- **WHEN** the Codex catalog is rendered
- **THEN** the list-visible source entry MUST take precedence for that slug
- **AND** the earlier hidden source MUST NOT suppress or replace it

#### Scenario: Bundled model appears on only one account in a plan

- **GIVEN** a same-plan refresh returns a bundled Codex model from one successful account but omits it from another
- **WHEN** the availability intersection excludes that model
- **THEN** the model MUST remain absent from live availability indexes
- **AND** its complete per-account live entry MUST refresh the metadata-only catalog
