## ADDED Requirements

### Requirement: Conversation archive toggles are audited with the actor

Enabling the conversation archive turns the proxy into a full recorder of prompt and response bodies readable by dashboard admins, so the settings API MUST write a dedicated audit event with action `conversation_archive_toggled` whenever a `PUT /api/settings` changes the effective value of `conversation_archive_enabled` — including a clear (`null`) that lets the environment variable or default take over with a different value. The event MUST record the new effective value (`enabled`), the layer it now comes from (`source`), the acting principal (`actor`, which MAY be null when the authentication mode carries no identity, and `actor_role`) and the actor IP, in addition to the ordinary `settings_changed` entry that lists `conversation_archive_enabled` among its changed fields. A `PUT` that stores the value already in effect, or only moves the setting between layers without changing the effective value, MUST NOT emit the dedicated event. The dashboard MUST require an explicit confirmation before it sends `conversation_archive_enabled: true`; the API contract itself is unchanged for other clients.

#### Scenario: Enabling the archive from the dashboard is audited

- **WHEN** an admin confirms enabling the conversation archive and the dashboard sends `PUT /api/settings` with `conversationArchiveEnabled: true`
- **THEN** an audit row with action `conversation_archive_toggled` is written
- **AND** its details record `enabled: true`, `source: "dashboard"`, the actor role and the actor identity when known
- **AND** the `settings_changed` audit row lists `conversation_archive_enabled` in `changed_fields`

#### Scenario: Clearing the dashboard value that turns the archive off is audited

- **GIVEN** the archive is enabled from the dashboard and the environment variable is unset
- **WHEN** an admin clears the dashboard value (`conversationArchiveEnabled: null`)
- **THEN** an audit row with action `conversation_archive_toggled` records `enabled: false` and `source: "default"`

#### Scenario: Re-saving the same value is not a toggle

- **GIVEN** the archive is enabled from the dashboard
- **WHEN** `PUT /api/settings` stores `conversationArchiveEnabled: true` again
- **THEN** no new `conversation_archive_toggled` audit row is written

#### Scenario: The dashboard does not enable without confirmation

- **WHEN** an admin flips the archive switch on in the dashboard
- **THEN** a confirmation dialog states that all prompt/response bodies will be written to each replica's local archive directory
- **AND** no request is sent until the dialog's confirm action is used; cancelling leaves the setting unchanged
