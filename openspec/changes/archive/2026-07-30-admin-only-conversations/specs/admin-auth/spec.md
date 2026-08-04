## ADDED Requirements

### Requirement: Guest conversation reads require an admin principal

Conversation list and detail routes are not guest-safe dashboard reads and MUST
require an `admin` principal. Existing guest-safe GET behavior and the existing
read-only write restrictions SHALL remain unchanged.

#### Scenario: Guest cannot read conversations

- **WHEN** a guest principal requests `GET /api/conversations`, `GET /api/conversations/`, or `GET /api/conversations/{id}`
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no conversation list or detail payload is returned

#### Scenario: Admin can read conversations

- **WHEN** an admin principal requests a conversation list or detail route
- **THEN** the request succeeds with the existing conversation response contract
