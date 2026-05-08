## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration. API key create and edit dialogs SHALL allow adding peer fallback base URLs directly to the key.

#### Scenario: Save prompt-cache affinity TTL
- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: Add peer fallback URLs to an API key
- **WHEN** a user creates or edits an API key
- **THEN** the app allows adding peer fallback base URLs for that key without opening Settings
- **AND** submits the URL list with the API key payload

#### Scenario: Settings omits peer fallback catalog
- **WHEN** a user opens the Settings page
- **THEN** the app does not show a peer fallback target catalog section

#### Scenario: View sticky-session mappings
- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward
