## ADDED Requirements

### Requirement: Dashboard guest access is read-only

The system SHALL support a dashboard `guest` role with read permission and without write permission. The system SHALL continue to treat password-authenticated, trusted-header, disabled-auth, and local bootstrap users as `admin` principals with read and write permissions.

#### Scenario: Guest can read dashboard APIs

- **WHEN** guest access is enabled and a guest principal requests a dashboard GET endpoint
- **THEN** the request succeeds using read-only dashboard access
- **AND** the session response identifies the principal as `guest`
- **AND** the session response includes only the `read` permission

#### Scenario: Guest cannot mutate dashboard state

- **WHEN** guest access is enabled and a guest principal requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`
- **AND** no dashboard state is changed

### Requirement: Guest access may be enabled without a guest password

The system SHALL allow operators to enable guest access without configuring a guest password. When guest access is enabled and no guest password is configured, remote dashboard requests that do not have an admin session SHALL be authorized as a `guest` principal for read-only routes.

#### Scenario: Passwordless guest reads remotely

- **WHEN** guest access is enabled
- **AND** no guest password is configured
- **AND** a remote request has no admin dashboard session
- **THEN** dashboard GET endpoints treat the request as a `guest`

#### Scenario: Passwordless guest still cannot write

- **WHEN** guest access is enabled without a guest password
- **AND** a remote request has no admin dashboard session
- **THEN** dashboard mutating endpoints return HTTP 403 with error code `read_only_access`

### Requirement: Guest access may require a guest password

The system SHALL allow operators to configure a separate guest password. When guest access is enabled and a guest password is configured, unauthenticated remote dashboard requests SHALL remain blocked until the guest password login endpoint issues a guest session.

#### Scenario: Password-protected guest login succeeds

- **WHEN** guest access is enabled with a guest password
- **AND** a remote client submits the correct guest password
- **THEN** the system issues a dashboard session with role `guest`
- **AND** subsequent dashboard GET endpoints are allowed

#### Scenario: Password-protected guest write is denied

- **WHEN** a password-authenticated guest session requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`
