# Responses API compatibility delta

## ADDED Requirements

### Requirement: Selected Codex installation identity is internally consistent

For native Codex requests, the service MUST use an account-specific installation id consistently.
When that id is applied, the service MUST use the same id in `x-codex-installation-id` and in
an existing `x-codex-turn-metadata.installation_id` field on every upstream
Responses transport. Missing, malformed, or non-object turn metadata MUST be
preserved rather than invented or discarded.

#### Scenario: Both canonical metadata carriers are present in a payload

- **WHEN** a native Responses payload contains both installation metadata
  carriers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Both canonical metadata carriers are present in headers

- **WHEN** a native HTTP or WebSocket request carries both installation
  metadata headers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Turn metadata cannot be safely rewritten

- **WHEN** `x-codex-turn-metadata` is malformed JSON, is not a JSON object, or
  does not contain `installation_id`
- **THEN** the service preserves that turn metadata unchanged
- **AND** it still applies the selected account id through the standalone
  installation-id carrier
