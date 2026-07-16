## MODIFIED Requirements

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the request has no account-scoped file requirement. A request with an account-scoped file requirement MUST remain bound to its file owner. An HTTP bridge request whose upstream `response.created` has assigned a response id MUST NOT be replayed on a replacement upstream connection, even when no later output event has reached the client.

#### Scenario: unanchored bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request is safely replayable before
  `response.created`
- **AND** it has no account-scoped file requirement
- **THEN** the bridge excludes the stalled account before reconnecting

#### Scenario: bridge response fails after response creation

- **WHEN** an HTTP bridge request has received `response.created`
- **AND** a later terminal error would otherwise select a replacement account
- **THEN** the bridge forwards the terminal error without replaying the request
- **AND** the client-visible response id is never reused for a replacement upstream response

#### Scenario: file-backed bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request requires its file-owner account
- **AND** it is retried before `response.created`
- **THEN** the bridge does not exclude or clear the required file owner
