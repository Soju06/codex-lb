## ADDED Requirements

### Requirement: Security audit reads require an admin principal

The dashboard security-audit route MUST require an admin principal. A guest
MUST receive HTTP 403 with `admin_access_required` and MUST NOT receive an audit
row, actor IP, identifying detail, or request ID. An admin MUST retain the
existing response contract including `actorIp`, `details`, and `requestId`.

#### Scenario: Guest cannot read security-audit records

- **GIVEN** an audit row contains identifying fields
- **WHEN** a guest requests `GET /api/audit-logs`
- **THEN** the response is HTTP 403 `admin_access_required`
- **AND** no identifying audit value is returned

#### Scenario: Admin retains security-audit detail

- **WHEN** an admin requests the same row
- **THEN** the request succeeds
- **AND** actor IP, details, and request ID remain present
