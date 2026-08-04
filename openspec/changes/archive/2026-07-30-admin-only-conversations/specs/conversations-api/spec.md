## ADDED Requirements

### Requirement: Conversation list and detail routes require an admin principal

The `/api/conversations` collection aliases and
`/api/conversations/{id}` detail route MUST require an `admin` dashboard
principal before reading conversation data. A non-admin principal MUST receive
HTTP 403 with error code `admin_access_required`; the route MUST NOT return a
conversation payload. Admin requests SHALL retain all existing membership,
windowing, timestamp, aggregation, pagination, and response-schema behavior.

#### Scenario: Guest collection access is denied

- **WHEN** a guest principal requests `/api/conversations` or `/api/conversations/`
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no conversation list is returned

#### Scenario: Guest detail access is denied

- **WHEN** a guest principal requests `/api/conversations/{id}`
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no conversation detail is returned

#### Scenario: Admin conversation access is unchanged

- **WHEN** an admin principal requests a collection or detail route
- **THEN** the existing conversation response is returned
