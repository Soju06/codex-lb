## ADDED Requirements

### Requirement: API keys settings expose quota privacy toggle
The Settings page SHALL include a toggle in the API Keys section that controls `hide_upstream_quota_from_api_keys`.

#### Scenario: Toggle is visible with the API keys controls

- **WHEN** the Settings page renders the API Keys section
- **THEN** the quota privacy toggle SHALL be shown alongside the API key auth toggle

#### Scenario: Toggle persists through settings save

- **WHEN** the user changes the quota privacy toggle
- **THEN** the settings update request SHALL include `hideUpstreamQuotaFromApiKeys`
