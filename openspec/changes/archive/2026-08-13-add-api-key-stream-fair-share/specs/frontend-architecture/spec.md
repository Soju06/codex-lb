# frontend-architecture

## ADDED Requirements

### Requirement: Fair-share congestion threshold is configurable from routing settings

The dashboard routing settings MUST expose the API-key fair-share congestion threshold as a numeric field adjacent to the per-account capacity limits, accepting integers from 0 to 100 where 0 disables the gate, with null-inherits-environment semantics matching the per-account capacity overrides. Values outside 0-100 MUST be rejected by both the client-side validation and the settings API. The field's label, description, and validation copy MUST be localized in the en, ko, and zh-CN locale bundles.

#### Scenario: Threshold round-trips through the settings API

- **GIVEN** an operator sets the threshold to 80 in routing settings
- **WHEN** the settings are saved and reloaded
- **THEN** the field shows 80 and the settings API reports 80 as the effective value

#### Scenario: Migrated null row inherits the environment default

- **GIVEN** a deployment whose dashboard settings row predates the field (a migrated NULL column)
- **AND** an environment-configured threshold
- **WHEN** the effective settings are read
- **THEN** the effective value inherits the environment setting

#### Scenario: Out-of-range values are rejected

- **GIVEN** an operator enters 101 or a negative number
- **WHEN** they attempt to save
- **THEN** the client blocks the save and the settings API rejects the value if submitted directly

#### Scenario: Copy is localized in all three locales

- **GIVEN** the dashboard language is set to en, ko, or zh-CN
- **WHEN** routing settings render
- **THEN** the threshold label and description display in the selected locale
