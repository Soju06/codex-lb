## ADDED Requirements

### Requirement: OAuth callback query credentials stay out of logs

The loopback OAuth callback server MUST NOT emit authorization codes, state
tokens, or the raw callback query string through its generic access log. It
MUST preserve callback routing and handler responses when access logging is
suppressed.

#### Scenario: Successful callback omits query credentials

- **GIVEN** the real loopback callback server is running
- **WHEN** a client requests `/auth/callback` with authorization-code and state query values
- **THEN** the callback handler response is returned unchanged
- **AND** neither query value is emitted by the callback access logger under
  text or JSON logging configuration

#### Scenario: Access suppression is callback-local

- **WHEN** the loopback OAuth callback server suppresses its access record
- **THEN** global application and proxy access-logging configuration remains unchanged
