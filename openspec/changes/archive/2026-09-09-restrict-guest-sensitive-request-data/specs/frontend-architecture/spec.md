## ADDED Requirements

### Requirement: Guest request details hide sensitive metadata and archives

The Request Details dialog MUST render full User Agent, Client IP, Conversation ID controls, and the conversation archive panel only for an admin dashboard principal. It MUST omit those fields and MUST NOT mount the archive panel for a guest principal. Request status, model, transport, timing, token, cost, and error details MUST remain available to guests.

#### Scenario: Guest opens request details

- **GIVEN** the authenticated dashboard principal has role `guest`
- **WHEN** the guest opens a request-log detail dialog
- **THEN** the dialog does not render User Agent, Client IP, Conversation ID, or archive controls
- **AND** it continues to render the non-identifying operational request details

#### Scenario: Admin opens request details

- **GIVEN** the authenticated dashboard principal has role `admin`
- **WHEN** the admin opens a request-log detail dialog
- **THEN** the dialog renders the identifying metadata fields when present
- **AND** it mounts the archive panel using `archiveRequestId` with the existing `requestId` fallback
