## MODIFIED Requirements

### Requirement: Dashboard settings page exposes password session lifetime

The SPA settings page SHALL expose a dashboard password session lifetime control for operators when password management is enabled. The control SHALL display the current configured lifetime, validate an operator-supplied value against the backend minimum, and save the new lifetime through the existing settings API. When the configured lifetime exceeds 30 days, the SPA SHALL show a warning that the longer lifetime increases the impact of a leaked browser profile or stolen cookie.

#### Scenario: Admin updates dashboard password session lifetime

- **WHEN** an admin opens the Settings page and changes the dashboard session lifetime value
- **THEN** the SPA submits the updated lifetime through `/api/settings`
- **AND** the saved settings response reflects the new lifetime value

#### Scenario: Admin chooses a long dashboard session lifetime

- **WHEN** an admin enters a dashboard session lifetime greater than 30 days
- **THEN** the Settings page shows a warning explaining that the longer lifetime increases the impact of a leaked browser profile or stolen cookie
- **AND** the admin can still save the configured lifetime
