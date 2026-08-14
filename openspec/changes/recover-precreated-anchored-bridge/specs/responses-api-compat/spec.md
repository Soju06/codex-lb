## ADDED Requirements

### Requirement: Eventless hard continuations receive one bounded recovery

When an HTTP Responses bridge request has a hard continuity owner and its
upstream socket reaches the missing-`response.created` watchdog before any
upstream response event, the service MUST allow at most one same-account,
same-`previous_response_id` reconnect and resend if all of the following hold:
the request has no assigned response id, no downstream sequence or visible
output, no upstream model output, and remains pending under the current bridge
session. The retry MUST preserve the existing durable operation fence,
admission leases, API-key reservation lifecycle, and account ownership. If the
bounded recovery does not succeed, the service MUST use the existing terminal
retirement path.

#### Scenario: Silent hard continuation reconnects once

- **GIVEN** a hard HTTP bridge request carries `previous_response_id`
- **AND** the first upstream socket receives `response.create` but emits no
  response event before the client-safe watchdog deadline
- **WHEN** the watchdog handles the timeout
- **THEN** the bridge reconnects on the same account and resends the unchanged
  anchored request at most once
- **AND** a replacement socket that emits a normal response completes the
  original HTTP request without requiring a client retry

#### Scenario: Eventless recovery remains fail-closed after one attempt

- **GIVEN** the first replacement socket also fails before any response event
- **WHEN** the bounded recovery is exhausted
- **THEN** the request is settled through the existing terminal failure path
- **AND** the bridge session is retired rather than retried indefinitely

#### Scenario: Unsafe continuation is not replayed

- **GIVEN** an anchored request has a response id, any response event, model
  output, downstream-visible output, downstream sequence, soft affinity, or a
  file-pinned account requirement
- **WHEN** the upstream socket becomes eventless before completion
- **THEN** the bridge MUST NOT use the same-anchor eventless recovery
- **AND** it MUST preserve the existing fail-closed behavior
