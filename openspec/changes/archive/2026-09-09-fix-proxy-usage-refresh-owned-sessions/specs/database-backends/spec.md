# database-backends Delta

## ADDED Requirements

### Requirement: Proxy usage refresh does not retain sessions across upstream I/O

The public proxy usage payload path MUST detach rows loaded for its initial
refresh decision before closing the request-adjacent repository scope. An owned
usage refresh MUST use caller-independent short-lived repositories for freshness
reads, upstream fetches, and required writes; it MUST NOT retain an
`AsyncSession` while waiting for upstream usage I/O. The payload path MUST
reopen a repository scope only after the refresh completes.

#### Scenario: cancelled usage request closes its initial scope

- **GIVEN** `/api/codex/usage` starts an owned usage refresh
- **WHEN** the client request is cancelled while the refresh is in flight
- **THEN** the initial repository scope is closed before the owned refresh runs
- **AND** the owned refresh remains caller-independent and may finish safely
- **AND** the payload-read repository scope is not reopened by the cancelled request

#### Scenario: usage refresh releases its session during upstream fetch

- **GIVEN** an owned usage refresh needs to fetch usage from an upstream service
- **WHEN** the upstream request is in flight
- **THEN** no database session remains checked out for that refresh's read/write work
- **AND** the refresh reacquires short-lived sessions only for required database operations
