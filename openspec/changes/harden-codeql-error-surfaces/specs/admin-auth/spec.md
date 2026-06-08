## ADDED Requirements

### Requirement: Dashboard OAuth callback errors hide internal exception details

Dashboard OAuth manual-callback responses MUST NOT include raw unexpected
exception strings, stack traces, local file paths, or other internal diagnostic
text in the response body. The server MAY log unexpected exceptions for operator
troubleshooting. User-actionable OAuth provider errors MAY continue to expose the
explicit provider-facing error code/message.

#### Scenario: Unexpected manual callback exception is sanitized

- **GIVEN** a dashboard session is authorized
- **AND** the OAuth manual-callback service raises an unexpected exception whose
  message contains internal diagnostic text
- **WHEN** the client calls `POST /api/oauth/manual-callback`
- **THEN** the response returns HTTP 500 with error code `manual_callback_failed`
- **AND** the response message is a generic internal-error message
- **AND** the response body does not contain the raw exception text

#### Scenario: OAuth provider error remains user-actionable

- **GIVEN** a dashboard session is authorized
- **AND** the OAuth manual-callback service raises `OAuthError` with an explicit
  error code and message
- **WHEN** the client calls `POST /api/oauth/manual-callback`
- **THEN** the response exposes that OAuth error code and message to the client
