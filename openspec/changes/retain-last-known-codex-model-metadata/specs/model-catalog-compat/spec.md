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

#### Scenario: Sol metadata remains resolvable after a partial refresh

- **GIVEN** a successful live catalog refresh returned complete metadata for `gpt-5.6-sol`
- **WHEN** a later successful refresh omits `gpt-5.6-sol`
- **THEN** the Codex catalog includes the last complete Sol metadata with hidden visibility
- **AND** `/v1/models` and live availability indexes omit Sol

#### Scenario: A later live entry replaces retained metadata

- **GIVEN** metadata was retained for a model omitted by a previous refresh
- **WHEN** a later live refresh returns that model with updated metadata
- **THEN** the updated live metadata is used and the model follows its current live visibility
