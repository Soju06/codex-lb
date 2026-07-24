## ADDED Requirements

### Requirement: Fresh HTTP bridge continuation reattach avoids poisoned anchors

The service MUST avoid sending a safely removable proxy-injected durable anchor as the first request on a fresh upstream WebSocket.

When an HTTP Responses request resolves durable continuity but no reusable
local bridge or active remote bridge owner exists, the service MUST NOT repeat
the same proxy-injected `previous_response_id` as the first request on newly
created upstream WebSockets when the client supplied a verified self-contained
full resend. It MUST use the existing raw-prefix proof, account-neutral input
projection, retained-output proof, opaque-state rejection, affinity stripping,
and server-namespaced recovery lane to submit the projected request once
without `previous_response_id`.

A client-supplied `previous_response_id` with the same verified replay body MAY
be attempted on the required owner. If that fresh-bridge attempt remains
completely eventless before `response.created`, the service MUST retry at most
once with the verified fresh body and without the anchor. It MUST NOT perform
that replay after downstream-visible output or matched response lifecycle
evidence.

A local bridge that is already marked to retire MUST NOT suppress fresh-bridge
recovery merely because an older visible request is still draining on that
bridge. Retry arbitration MUST be serialized with original dispatch and
downstream detach. The startup deadline and reconnect MUST remain clamped to
the original HTTP request budget, and a failed reconnect MUST terminate the
expired request rather than disarm its watchdog.

Payloads containing incomplete history, unresolved calls, conversations,
file ownership, or encrypted, opaque, unknown, or account-scoped state that
cannot be safely removed by the existing projection MUST remain fail-closed.
Known response-owned reasoning and hosted-search bookkeeping MAY be removed by
that projection. A transport error after an ambiguous send MUST NOT by itself
authorize replay.

#### Scenario: Proxy-injected fresh anchor is projected before dispatch

- **GIVEN** a request omits `previous_response_id` but carries a verified full resend for a durable bridge
- **AND** neither a live local bridge nor an active remote owner can receive the request
- **WHEN** the proxy would otherwise inject the durable response id into a fresh upstream WebSocket
- **THEN** it sends one validated projected request without `previous_response_id`
- **AND** it does not first send the poisoned anchored shape

#### Scenario: Client anchor receives one bounded fresh replay

- **GIVEN** a client-supplied durable anchor and verified full resend are sent on a fresh bridge
- **AND** upstream emits no response lifecycle event or downstream-visible output
- **WHEN** the fresh-reattach startup deadline expires
- **THEN** the proxy retires the silent upstream and retries once without the anchor
- **AND** another silent attempt terminates instead of repeating the replay

#### Scenario: Unsafe continuation is not replayed

- **GIVEN** a fresh continuation contains owner-bound or incomplete state
- **WHEN** its anchored upstream send is silent or fails ambiguously
- **THEN** the proxy does not remove the anchor, change accounts, or resend it as a fresh turn

#### Scenario: Draining local bridge does not hide a fresh reattach

- **GIVEN** durable continuity resolves to a local bridge that is already retiring
- **AND** an older visible request remains on that draining bridge
- **WHEN** a new continuation must create a replacement upstream WebSocket
- **THEN** the proxy applies the fresh-bridge projection or bounded replay rules

#### Scenario: Retry failure remains bounded by the original request

- **GIVEN** a verified fresh reattach reaches its eventless startup deadline
- **WHEN** same-account reconnect fails or the original request budget expires
- **THEN** the proxy emits a terminal failure without extending or disarming the original deadline
