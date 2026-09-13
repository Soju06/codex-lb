## ADDED Requirements

### Requirement: Affected Settings dialogs restore invoker focus

The Settings `View collected data` telemetry preview and `Set password` setup dialogs SHALL retain the exact button that invoked them. When either dialog is dismissed with Escape or its explicit Close/Cancel action, the dialog SHALL restore focus to that connected invoking button without changing the Settings page scroll position. After restoration, `document.body` MUST NOT be the active element.

Focus restoration MUST preserve the telemetry preview's on-demand fetch and conditional mounting behavior and the password setup flow's authentication request, session refresh, toast, form reset, and conditional mounting behavior. Password change, remove, verify, and TOTP dialogs are outside this requirement.

#### Scenario: Telemetry preview closes with Escape

- **GIVEN** an operator opened `View collected data` from its Settings button
- **WHEN** the operator presses Escape
- **THEN** the preview dialog closes
- **AND** focus returns to that exact `View collected data` button without scrolling Settings
- **AND** `document.body` is not active

#### Scenario: Telemetry preview closes explicitly

- **GIVEN** an operator opened `View collected data` from its Settings button
- **WHEN** the operator activates the dialog's Close action
- **THEN** the preview dialog closes
- **AND** focus returns to that exact `View collected data` button without scrolling Settings
- **AND** `document.body` is not active

#### Scenario: Password setup closes with Escape

- **GIVEN** an operator opened password setup from the `Set password` button
- **WHEN** the operator presses Escape
- **THEN** the setup dialog closes without submitting password setup
- **AND** focus returns to that exact `Set password` button without scrolling Settings
- **AND** `document.body` is not active

#### Scenario: Password setup closes explicitly

- **GIVEN** an operator opened password setup from the `Set password` button
- **WHEN** the operator activates Cancel
- **THEN** the setup dialog closes without submitting password setup
- **AND** focus returns to that exact `Set password` button without scrolling Settings
- **AND** `document.body` is not active
