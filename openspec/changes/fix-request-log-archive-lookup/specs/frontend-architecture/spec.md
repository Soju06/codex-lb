## MODIFIED Requirements

### Requirement: Dashboard request-log archive lookup
The dashboard request-log detail dialog SHALL use each row's `archiveRequestId` for conversation archive lookup when that field is present. For older API responses that omit `archiveRequestId`, it SHALL fall back to the row's `requestId`.

#### Scenario: Detail dialog uses archive lookup id
- **WHEN** a request-log row has `requestId: "resp_123"` and `archiveRequestId: "req_123"`
- **AND** the operator opens the request detail dialog
- **THEN** the archive panel queries archive records for `req_123`

#### Scenario: Detail dialog remains backward compatible
- **WHEN** a request-log row does not include `archiveRequestId`
- **AND** the operator opens the request detail dialog
- **THEN** the archive panel queries archive records for the row's `requestId`
