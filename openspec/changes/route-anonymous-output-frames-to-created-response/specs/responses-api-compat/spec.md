## ADDED Requirements

### Requirement: Anonymous upstream output frames belong to the created response

When more than one request is pending on a single upstream WebSocket (an HTTP bridge session or a direct WebSocket session) and an upstream `response.*` frame other than `response.completed`, `response.failed` or `response.incomplete` arrives without a response id, the proxy MUST deliver that frame to the pending request whose response upstream has already created (its response id is known), when exactly one such request exists, regardless of whether that request is still visible or is draining after downstream cancellation. The proxy MUST NOT deliver such a frame to a sibling request that is still waiting for its own `response.created`. Anonymous `error`, `response.completed`, `response.failed` and `response.incomplete` frames and vendor telemetry frames that are not `response.*` events (for example `codex.rate_limits`) MUST keep their existing ownership rules, including targeting the request whose `response.create` is still unacknowledged. When no pending request, or more than one pending request, already has a response id, the pre-existing ownership rules apply unchanged.

When payload archiving is enabled on the direct WebSocket path, archive attribution MUST use the same anonymous output ownership rule as relay processing, including attribution to a draining created owner.

#### Scenario: Archive and relay agree on the created owner

- **GIVEN** a direct WebSocket with payload archiving enabled, request A with a known response id, and request B waiting for its own `response.created`
- **WHEN** an anonymous `response.output_text.delta` arrives
- **THEN** the frame is archived under A's archive request id, not B's
- **AND** relay processing accounts the frame to A and forwards its bytes unchanged

#### Scenario: Pipelined sibling does not receive the created response's output

- **GIVEN** an HTTP bridge session with request A whose `response.created` has arrived
- **AND** request B on the same session has sent `response.create` and is waiting for its own `response.created`
- **WHEN** upstream emits `response.output_item.added` and `response.output_text.delta` without a response id
- **THEN** both frames are delivered to request A's downstream stream
- **AND** request B's downstream stream receives nothing

#### Scenario: Draining sibling that gave up does not swallow the created response's output

- **GIVEN** request A's `response.created` has arrived on a shared bridge session
- **AND** request B on the same session is draining after its downstream closed before its own `response.created`
- **WHEN** upstream emits an anonymous output frame
- **THEN** the frame is delivered to request A

#### Scenario: Cancelled created response keeps its output away from a visible sibling

- **GIVEN** request A's `response.created` has arrived and A is draining after downstream cancellation
- **AND** request B on the same session is visible and waiting for its own `response.created`
- **WHEN** upstream emits an anonymous output frame
- **THEN** the frame is attributed to request A's drain
- **AND** request B's downstream stream receives nothing

#### Scenario: Anonymous error still targets the unacknowledged request

- **GIVEN** request A's `response.created` has arrived on a shared bridge session
- **AND** request B on the same session is waiting for its own `response.created`
- **WHEN** upstream emits an `error` frame without a response id
- **THEN** request B is failed with that error
- **AND** request A remains pending and receives nothing

#### Scenario: Anonymous completion keeps existing terminal ownership

- **GIVEN** an HTTP bridge or direct WebSocket with two visible pending requests, A with a known response id and B waiting for its own `response.created`
- **WHEN** an anonymous `response.completed` arrives
- **THEN** B is removed and finalized while A remains pending and receives no event accounting
- **AND** the HTTP bridge delivers the completion to B's queue
- **AND** direct WebSocket archive attribution uses B's archive request id and forwards the frame unchanged

#### Scenario: Leading telemetry still targets the unacknowledged request

- **GIVEN** request A's `response.created` has arrived on a shared bridge session
- **AND** request B on the same session has just sent `response.create`
- **WHEN** upstream emits a `codex.rate_limits` frame without a response id
- **THEN** the frame is attributed to request B

#### Scenario: Two visible created responses leave an anonymous frame unmatched

- **GIVEN** exactly two requests are pending on one upstream socket, both visible and both with a response id
- **WHEN** upstream emits an anonymous output frame
- **THEN** the frame matches no request and is recorded as unmatched upstream liveness, as before this change
