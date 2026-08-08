## ADDED Requirements

### Requirement: Settings manages one global OAuth Live policy

The Settings page SHALL expose one Live Voice card with a global OAuth enable switch, an explicit allowed-upstream Account multi-select, and one save action. Active state with an empty pool MUST be blocked with actionable validation. Read-only users SHALL see current state while all mutations remain disabled.

#### Scenario: Operator enables OAuth Live globally

- **GIVEN** one or more selectable upstream Accounts
- **WHEN** a dashboard writer enables OAuth Live, selects Accounts, and saves
- **THEN** the app replaces the global policy atomically
- **AND** every locally admitted keyless OAuth caller uses that pool for subsequent call creation

#### Scenario: Operator revokes global access

- **WHEN** a dashboard writer disables and saves the policy
- **THEN** subsequent OAuth Live authorization fails before account selection
- **AND** registered Key callers retain their existing behavior

#### Scenario: Read-only inspection

- **WHEN** a read-only dashboard user views Live Voice settings
- **THEN** active state and selected upstream Accounts remain visible
- **AND** switch, selector, and save action remain disabled

#### Scenario: Selected Account becomes unavailable

- **GIVEN** an Account selected in the OAuth Live pool becomes paused, reauthentication-required, or deactivated
- **WHEN** a dashboard writer opens the compact Account selector
- **THEN** the selected unavailable Account remains identifiable and selected
- **AND** the writer can remove it while unselected unavailable Accounts remain hidden
