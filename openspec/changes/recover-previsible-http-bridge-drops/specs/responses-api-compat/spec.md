### Requirement: HTTP bridge websocket drops recover only before visible output

When an HTTP responses bridge upstream websocket closes while exactly one
non-draining request is pending, the service MUST retry that request at most
once on a fresh upstream websocket if replay cannot duplicate externally visible
effects. A request is replay-safe when no downstream-visible text or tool output
has been emitted, the request has a replay body, and any `previous_response_id`
dependency can either be preserved safely before `response.created` or replaced
with a retry-safe full-resend payload without the anchor. If replay is unsafe or
reconnect/resend fails, the service MUST fail the pending request with a
retryable `stream_incomplete` error and retire the affected bridge session.

#### Scenario: websocket closes before response.created

- **GIVEN** an HTTP bridge request is pending and no `response.*` event has been
  emitted downstream
- **WHEN** the upstream websocket closes without `response.completed`
- **THEN** the service reconnects and resends the request once when replay is
  safe
- **AND** the downstream stream receives the recovered upstream response events

#### Scenario: websocket closes after response.created but before visible output

- **GIVEN** an HTTP bridge request has emitted `response.created` but no
  downstream-visible text or tool output
- **WHEN** the upstream websocket closes without `response.completed`
- **THEN** the service reconnects and resends the request once when replay is
  safe
- **AND** the service preserves the original downstream response id and suppresses
  a duplicate `response.created` event from the retry

#### Scenario: websocket closes after visible output

- **GIVEN** an HTTP bridge request has emitted downstream-visible text or tool
  output
- **WHEN** the upstream websocket closes without `response.completed`
- **THEN** the service MUST NOT replay the request
- **AND** the pending request fails with retryable `stream_incomplete`

#### Scenario: unsafe replay skip is observable

- **GIVEN** an HTTP bridge request is pending when the upstream websocket closes
- **WHEN** the request is not replay-safe
- **THEN** the service logs an HTTP bridge event with the retry skip reason
- **AND** the event does not expose raw prompt, response, or account secrets
