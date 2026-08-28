## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads,
reset priority, prompt-cache affinity TTL), password management
(setup/change/remove), TOTP management (setup/disable), API key auth toggle,
API key management (table, create, edit, delete, regenerate), and
sticky-session administration. API key create/edit controls that expose
reasoning effort choices MUST include upstream-supported extended efforts such
as `max` and `ultra`.

#### Scenario: API key dialog offers extended reasoning efforts

- **WHEN** an operator opens the API key create or edit dialog
- **THEN** the enforced reasoning control offers `Max` and `Ultra` in addition to existing reasoning efforts

## ADDED Requirements

### Requirement: Automations page accepts extended reasoning efforts

The Automations page SHALL allow operators to create and update scheduled
refresh jobs using any reasoning effort advertised by the selected model,
including extended GPT-5.6 efforts such as `max` and `ultra`.

#### Scenario: Automation dialog offers extended model reasoning efforts

- **WHEN** a selected model advertises `max` or `ultra` in `supportedReasoningEfforts`
- **THEN** the automation create/edit dialog offers those efforts as selectable values
