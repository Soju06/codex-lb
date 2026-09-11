## MODIFIED Requirements

### Requirement: Conversation list and detail routes require an admin principal

The `/api/conversations` collection aliases and `/api/conversations/{id}` detail route MUST require the
`conversations:read` dashboard permission before reading conversation data; among the built-in roles only
`admin` holds it. A principal without that permission MUST receive HTTP 403 with error code
`permission_required` and `param` `conversations:read`; the route MUST NOT return a conversation payload.
Admin requests SHALL retain all existing membership, windowing, timestamp, aggregation, pagination, and
response-schema behavior.

#### Scenario: Guest collection access is denied

- **WHEN** a guest principal requests `/api/conversations` or `/api/conversations/`
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` `conversations:read`
- **AND** no conversation list is returned

#### Scenario: Guest detail access is denied

- **WHEN** a guest principal requests `/api/conversations/{id}`
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` `conversations:read`
- **AND** no conversation detail is returned

#### Scenario: Admin conversation access is unchanged

- **WHEN** an admin principal requests a conversation list or detail route
- **THEN** the request succeeds with the existing conversation response contract
