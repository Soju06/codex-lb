## ADDED Requirements

### Requirement: Speed and service tier metadata aggregates across accounts

When the model registry merges catalog entries for the same model slug fetched
from multiple plans or accounts, the system MUST union the model's
`service_tiers`, `additional_speed_tiers`, and `default_service_tier` metadata
across all contributing entries rather than overwriting them with the
last-fetched entry. A slug MUST expose a speed/service tier when at least one
contributing account advertises it, so an account without Fast entitlement
cannot remove Fast from the shared catalog served by `GET /v1/models` and
`GET /backend-api/codex/models`. Union entries MUST be de-duplicated. All
non-tier model fields MAY retain last-fetched values.

#### Scenario: An account without Fast does not hide Fast globally

- **GIVEN** one account/plan returns `gpt-5.5` with a `fast` service tier
- **AND** another account/plan returns `gpt-5.5` with no `fast` service tier
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.5` entry includes the `fast` service tier
- **AND** the `fast` entry appears exactly once

#### Scenario: Shared tiers are not duplicated

- **GIVEN** two accounts both return `gpt-5.5` with the same `fast` service tier
- **WHEN** the registry merges the two catalog snapshots
- **THEN** the merged `gpt-5.5` service tiers contain a single `fast` entry
