# frontend-architecture Specification

## Purpose

See context docs for background.
## Requirements
### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL), peer fallback target management, password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Save prompt-cache affinity TTL
- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: Manage peer fallback targets
- **WHEN** a user opens the peer fallback target section on the Settings page
- **THEN** the app fetches registered peer fallback targets
- **AND** allows adding, enabling/disabling, updating, and deleting targets through the dashboard API

#### Scenario: View sticky-session mappings
- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward
