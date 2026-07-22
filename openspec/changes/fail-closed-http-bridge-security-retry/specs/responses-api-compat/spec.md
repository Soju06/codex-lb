## ADDED Requirements

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. A permitted
file-free retry MUST select the replacement with cleared request and session
affinity, but MUST validate any raw legacy owner before changing the live
session or its durable owner generation. On success it MUST make exactly one
durable replacement claim before swapping the session, then clear or replace
the session affinity and local turn-state aliases. A legacy-owner conflict MUST
leave the original session open and unchanged. File-pinned requests MUST NOT
migrate.

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

#### Scenario: Legacy owner conflict fails before replacement mutation

- **GIVEN** a session-header security retry selects an authorized replacement account
- **AND** the raw legacy affinity row belongs to a different account
- **WHEN** the service validates the replacement
- **THEN** it does not claim the durable session for the replacement
- **AND** it leaves the original account, upstream, owner generation, aliases, and open session unchanged
