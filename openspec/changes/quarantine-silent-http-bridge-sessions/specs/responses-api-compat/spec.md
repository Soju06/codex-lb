## ADDED Requirements

### Requirement: Silent HTTP bridge submissions fail closed without replay

After an HTTP bridge session sends `response.create`, the service MUST bound
the wait for upstream to emit `response.created`. If the deadline expires, the
service MUST fail the current request terminally, retire and close the affected
bridge session, release its pending request and response-create admission
state, and temporarily quarantine that bridge affinity key.

The service MUST NOT automatically replay the same submitted request within
the current client call because upstream acceptance is ambiguous. During the
quarantine window, the next independent client retry for that affinity key MUST
bypass the HTTP bridge and use direct HTTP. If that retry carries
`previous_response_id`, it MUST remain pinned to the response owner's account.
A quarantined key MUST NOT prevent unrelated affinity keys or accounts from
making progress.

#### Scenario: Silent submission is retired without internal replay

- **WHEN** an HTTP bridge sends `response.create`
- **AND** upstream emits neither `response.created` nor a terminal event before the configured deadline
- **THEN** the current request fails with a retryable terminal error
- **AND** the bridge session is retired, removed from reuse, and closed
- **AND** its pending queue entry and response-create gate are released
- **AND** the service does not resend the request during the same client call

#### Scenario: Client retry bypasses a quarantined bridge

- **GIVEN** an affinity key was quarantined after a silent bridge submission
- **WHEN** the client independently retries before the quarantine expires
- **THEN** the service sends the retry over direct HTTP instead of creating or reusing an upstream websocket bridge
- **AND** the direct HTTP response is streamed through the existing Responses contract

#### Scenario: Previous-response fallback preserves account ownership

- **GIVEN** a quarantined retry carries `previous_response_id`
- **AND** the service can resolve the account that owns that response
- **WHEN** the retry is sent over direct HTTP
- **THEN** the retry uses the owning account
- **AND** the service does not move the response chain to another available account

#### Scenario: Silent bridge does not block unrelated accounts

- **GIVEN** one affinity key has an accepted-but-silent bridge submission on account A
- **WHEN** independent requests target eligible sessions on other accounts
- **THEN** those requests can complete without waiting for the silent bridge deadline

### Requirement: Downstream stream cancellation closes bridge lifecycle

When a downstream Responses streaming body is closed, the service MUST close
the bridge lifecycle iterator and release the associated pending request,
response-create gate, and upstream websocket resources. This requirement
applies even when the downstream consumed only the initial SSE heartbeat and
no upstream `response.created` event.

#### Scenario: Client closes after initial heartbeat

- **GIVEN** a Codex-native HTTP Responses stream has emitted its initial heartbeat
- **AND** upstream has not emitted `response.created`
- **WHEN** the downstream client closes the stream
- **THEN** the pending bridge request and queue admission are released
- **AND** the bridge session and upstream websocket are closed
