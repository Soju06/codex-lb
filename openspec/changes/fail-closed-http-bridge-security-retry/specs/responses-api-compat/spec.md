## ADDED Requirements

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. The retry MUST
clear stale turn affinity before a permitted file-free replacement attempt and
MUST restore the original safe state if reconnect fails. File-pinned requests
MUST NOT migrate.

#### Scenario: Created HTTP bridge response is not replayed

- **WHEN** an HTTP bridge request has emitted `response.created` before a
  security-work authorization denial
- **THEN** the service does not reconnect or resend the request on another account
- **AND** it forwards the original terminal error

#### Scenario: Deferred reasoning blocks replay

- **WHEN** an HTTP bridge request buffers a reasoning prelude before a
  security-work authorization denial
- **THEN** that prelude blocks account-switch replay and is not emitted before
  the terminal security decision
