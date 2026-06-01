## MODIFIED Requirements
### Requirement: Settings page
The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Save split sticky reallocation thresholds
- **WHEN** a user updates the primary or secondary sticky reallocation threshold from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated split threshold fields
- **AND** the saved settings response reflects both split sticky reallocation thresholds
