## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL, limit warm-up controls), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Save prompt-cache affinity TTL
- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: Save staggered idle warm-up setting
- **WHEN** a user toggles staggered idle limit warm-up from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated value and reflects the saved value

#### Scenario: View sticky-session mappings
- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward

### Requirement: Accounts list surfaces quota reset timing

The Accounts page account list SHALL render a compact 5h quota row and a weekly quota row for accounts that have both quota windows, and SHALL include the time remaining until reset for each rendered row when a reset timestamp is available. Weekly-only accounts SHALL omit the 5h row. Account cards SHALL also surface whether the account is opted in to limit warm-up.

#### Scenario: Regular account shows both quota rows
- **WHEN** the account list renders an account with both primary and weekly quota windows
- **THEN** the list item shows both 5h and weekly quota rows
- **AND** each rendered row shows its reset countdown

#### Scenario: Weekly-only account omits the 5h row
- **WHEN** the account list renders an account whose primary window is absent
- **THEN** the list item does not render a 5h quota row
- **AND** the weekly quota row still renders

#### Scenario: Limit warm-up opt-in is visible
- **WHEN** the account list renders an account with limit warm-up opt-in enabled
- **THEN** the account card shows the opt-in state
