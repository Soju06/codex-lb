## MODIFIED Requirements

### Requirement: Dashboard guest access is read-only

The system SHALL support a dashboard `guest` role with permission to read guest-safe dashboard data and without permission to read admin-only sensitive data or mutate state. Full conversation archives and request-log filtering by a dedicated conversation identifier MUST require an `admin` principal. The system SHALL continue to treat password-authenticated, trusted-header, disabled-auth, and local bootstrap users as `admin` principals with read and write permissions.

#### Scenario: Guest can read guest-safe dashboard APIs

- **WHEN** guest access is enabled and a guest principal requests a guest-safe dashboard GET endpoint
- **THEN** the request succeeds using read-only dashboard access
- **AND** the session response identifies the principal as `guest`
- **AND** the session response includes only the `read` permission

#### Scenario: Guest cannot read conversation archives

- **WHEN** a guest principal requests any conversation-archive endpoint
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no archive file metadata, payload, headers, or other archive record data is returned

#### Scenario: Guest cannot filter request logs by conversation identifier

- **WHEN** a guest principal requests request logs with the dedicated `conversation_id` filter
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no filtered rows, request count, or aggregated conversation cost is returned

#### Scenario: Guest cannot mutate dashboard state

- **WHEN** guest access is enabled and a guest principal requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`
- **AND** no dashboard state is changed
