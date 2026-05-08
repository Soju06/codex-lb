## MODIFIED Requirements

### Requirement: Dashboard manages peer fallback targets

The system SHALL allow authenticated dashboard users to create, list, update, enable/disable, and delete peer fallback targets. Each target MUST have a stable identifier, normalized absolute HTTP(S) base URL, enabled flag, creation timestamp, and update timestamp.

#### Scenario: Reject peer fallback target URL params, query, or fragment

- **WHEN** a dashboard user creates or updates a peer fallback target with path params, query, or fragment in the base URL
- **THEN** the system rejects the request with a dashboard validation error
- **AND** does not persist the invalid target
