## MODIFIED Requirements

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the request has no account-scoped file requirement. A request with an account-scoped file requirement MUST remain bound to its file owner. An HTTP bridge request whose upstream `response.created` has assigned a response id MUST NOT be replayed on a replacement upstream connection, even when no later output event has reached the client. The upstream reader MUST enforce `http_responses_session_bridge_response_created_timeout_seconds` as the per-attempt startup cutoff for requests sent upstream but still waiting for `response.created`; the default cutoff MUST be 120 seconds. If the cutoff expires, a single safely replayable unanchored request MAY be reconnected and resent before a terminal error is emitted, but a request containing account-scoped `input_file.file_id` references MUST NOT be replayed on another account solely because the startup cutoff elapsed. If replay is unavailable or unsafe, the request MUST fail with `error.code = "response_created_timeout"`. If another pending request shares the same upstream bridge, the timed-out request MUST fail and the shared bridge MUST be retired so late anonymous upstream events cannot attach to the wrong request.

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

#### Scenario: bridge request exceeds response-created startup cutoff

- **WHEN** an HTTP bridge request has been sent upstream
- **AND** upstream does not emit `response.created` within `http_responses_session_bridge_response_created_timeout_seconds`
- **AND** the request cannot be safely replayed before visible output
- **THEN** the request is removed from the bridge queue and failed with `error.code = "response_created_timeout"`
- **AND** any response-create gate or lease held by that request is released
- **AND** a file-backed request is not resent on another account solely because the cutoff elapsed

#### Scenario: multiplexed bridge startup cutoff retires shared upstream

- **WHEN** an HTTP bridge session has more than one pending request
- **AND** one pending request reaches the configured response-created startup cutoff
- **THEN** only the timed-out request is failed with `error.code = "response_created_timeout"`
- **AND** the shared bridge is retired before a late anonymous upstream `response.created` can be assigned to a sibling request
- **AND** remaining pending bridge work is failed with a retryable bridge/session error
