## ADDED Requirements

### Requirement: Published Codex providers enable authenticated model discovery

The published machine-local Codex configuration example MUST enable
`features.api_key_model_discovery` and MUST provide an explicit
`model_catalog_url` for each documented codex-lb provider. Each catalog URL
MUST use the same origin as that provider's `base_url` and target
`/backend-api/codex/models`.

The inline client setup examples MUST supply the same discovery settings.
Adding discovery settings MUST preserve provider selection, existing
authentication settings, and the separation between ordinary traffic and
opt-in capability routing.

#### Scenario: API-key provider has an explicit discovery path
- **WHEN** a user configures the published codex-lb provider with an API key in a Codex client supporting API-key model discovery
- **THEN** the example enables API-key discovery and supplies the native catalog URL
- **AND** the documented discovery configuration does not depend on a previously cached model list

#### Scenario: Opt-in provider retains isolated capability routing
- **WHEN** a user selects the documented opt-in capability provider
- **THEN** that provider also supplies an explicit native catalog URL
- **AND** its capability header remains absent from the ordinary provider
