## ADDED Requirements

### Requirement: OpenAI-compatible model metadata preserves speed tiers

When serving `GET /v1/models`, the system SHALL preserve upstream speed-tier metadata in each model's `metadata` object when upstream provides it. This includes `additional_speed_tiers`, `service_tiers`, and `default_service_tier`. The system MUST NOT invent speed tiers for models whose upstream catalog entry does not advertise them.

#### Scenario: /v1/models exposes upstream fast tier metadata

- **WHEN** the upstream model catalog contains `gpt-5.5` with `additional_speed_tiers=["fast"]`
- **AND** the upstream model catalog includes a `service_tiers` entry with `id="priority"` and `name="Fast"`
- **WHEN** a client calls `GET /v1/models`
- **THEN** the `gpt-5.5` entry's metadata includes `additional_speed_tiers=["fast"]`
- **AND** the metadata includes the upstream `service_tiers` entry
- **AND** the metadata includes the upstream `default_service_tier` when present
