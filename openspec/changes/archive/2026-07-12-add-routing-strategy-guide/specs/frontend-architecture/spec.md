## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL, routing strategy guidance), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Routing strategy guide is visible
- **WHEN** the Routing settings section renders
- **THEN** it shows operator guidance for the available routing strategies
- **AND** it identifies strategies that intentionally concentrate traffic so operators can choose them deliberately
