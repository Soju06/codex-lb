## ADDED Requirements

### Requirement: Accounts page manages OAuth Live Voice caller policy

The selected Account detail SHALL expose an OAuth Live Voice policy card to dashboard writers without adding a core navigation item or global setting. The card MUST display active state and an explicit allowed-account multi-select, MUST save the complete set transactionally, and MUST keep existing Account actions available. Enabling or saving an active policy with an empty allowed set MUST be rejected with actionable validation.

#### Scenario: Operator enables a caller policy

- **GIVEN** an imported caller Account and one or more selectable upstream Accounts
- **WHEN** a dashboard writer enables OAuth Live Voice and saves an allowed set
- **THEN** the app persists the active policy and complete allowed-account set
- **AND** refreshes the selected Account policy view

#### Scenario: Operator revokes a caller policy

- **WHEN** a dashboard writer disables or deletes the selected Account's policy
- **THEN** subsequent OAuth Live authorization for that caller fails before upstream account selection
- **AND** existing reauth, export, pause, resume, and delete actions remain available

