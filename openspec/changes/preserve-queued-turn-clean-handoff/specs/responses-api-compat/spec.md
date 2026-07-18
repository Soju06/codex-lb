## ADDED Requirements

### Requirement: Direct WebSocket receive ownership survives upstream handoff

For each direct `/backend-api/codex/responses` or `/v1/responses` WebSocket connection, the proxy MUST maintain at most one owned downstream receive operation while waiting for the next client frame. Receive polling timeouts, idle-close rechecks, and clean retirement of a completed upstream generation MUST preserve that same operation until it yields a frame or the downstream session terminates; the proxy MUST NOT cancel and replace the operation merely because the upstream reader completed.

Whenever upstream completion and a retained downstream receive outcome are both observable before a replay side effect starts, the proxy MUST inspect the receive outcome first. This requirement applies when both operations complete in one wait cycle and when either outcome becomes observable later during upstream retirement, request preparation, continuity-owner lookup, or response-create admission. If the receive failed or was cancelled, the proxy MUST detach ownership, mark the downstream unusable, and propagate that outcome before starting another replay side effect. If it yielded `websocket.disconnect`, the proxy MUST detach the result, mark the downstream disconnected, and stop the session. If it yielded `websocket.receive` successfully, the proxy MUST retain the completed result, retire the upstream generation, and handle any earlier replay before processing the newer frame. The proxy MUST NOT give replay precedence over any other downstream receive outcome.

When an earlier replay becomes observable after a newer `response.create` has been prepared but before it has been sent, the proxy MUST retain exactly one owned prepared request, preserve its quota reservation, and prevent it from being admitted or sent ahead of the replay. If response-create admission was already acquired when the replay became observable, the proxy MUST release that admission before replay and reacquire it when processing the deferred request. After the replay completes, the proxy MUST register and send the deferred request exactly once without preparing or reserving it again.

Before starting a replacement connection, acquiring replay response capacity, or sending replay text, the proxy MUST recheck any completed retained receive. If a terminal receive becomes observable while replacement connection or capacity acquisition is already in flight, the proxy MAY finish that in-flight operation, but it MUST take cleanup ownership of every returned upstream and lease, MUST NOT send the replay, and MUST close or release those resources before exit. This ordering MUST prevent newer client work from overtaking an earlier replay or being sent to a retired upstream generation, and MUST prevent replay after a terminal downstream outcome whenever that outcome is observable before send begins.

On every downstream session exit, including disconnect, idle close, cancellation, upstream-handoff failure, or downstream-receive exception, the proxy MUST detach ownership before cancelling, awaiting, or observing the receive operation. A secondary completed-receiver exception discovered during cleanup MUST be logged and contained so it does not replace an active primary failure or prevent upstream closure, account or admission lease release, response-create gate release, quota-reservation release, and pending, replay, or deferred-request settlement. Cleanup MUST leave no orphan receiver or prepared request and MUST NOT start a second concurrent receiver.

#### Scenario: queued turn survives clean upstream retirement

- **GIVEN** a direct Responses WebSocket remains open after a terminal response
- **AND** one downstream receive operation owns the wait for the next client frame
- **WHEN** the completed upstream generation closes cleanly while the client's next `response.create` becomes available
- **THEN** the proxy retires that upstream generation without cancelling or replacing the downstream receive operation
- **AND** the queued request is processed exactly once after the handoff
- **AND** the downstream WebSocket remains open for the request's response events

#### Scenario: upstream replay wins a successful simultaneous completion

- **GIVEN** an upstream generation completes with an earlier request eligible for replay
- **AND** the downstream receive successfully yields a newer client frame in the same wait cycle
- **WHEN** the proxy observes both completed awaitables
- **THEN** it captures and handles the earlier replay before processing the newer frame
- **AND** it retains and later processes the completed downstream receive result exactly once
- **AND** the newer frame is not sent to the retired upstream generation

#### Scenario: replay becomes visible after a newer request is prepared

- **GIVEN** a newer `response.create` has been received and prepared while the previous upstream reader is still running
- **WHEN** that reader exposes an earlier replay during preparation, owner lookup, or response-create admission
- **THEN** the proxy retains the prepared newer request without reserving or preparing it again
- **AND** it cancels or releases any incomplete admission for the newer request
- **AND** it completes the earlier replay before admitting and sending the newer request exactly once

#### Scenario: terminal receive becomes visible during upstream retirement

- **GIVEN** the wait site first reports only an upstream generation eligible for replay
- **AND** the retained downstream receive has not completed at that observation point
- **WHEN** the receive fails, is cancelled, or disconnects while the old upstream is closing or its stream lease is being released
- **THEN** the proxy observes the terminal outcome before starting a replacement connection
- **AND** it does not acquire replay admission or response capacity or resend the replay
- **AND** it releases the old upstream, stream and response leases, response-create gate, quota reservation, and replay state

#### Scenario: terminal receive completes while reconnect is in flight

- **GIVEN** replay reconnect began while the retained downstream receive was still pending
- **WHEN** the receive becomes terminal before that reconnect returns
- **THEN** the proxy may accept the reconnect result only to assume cleanup ownership
- **AND** it closes the replacement upstream and releases its stream lease
- **AND** it does not create a replacement reader, acquire replay response capacity, or resend the replay

#### Scenario: failed simultaneous receive prevents replay

- **GIVEN** an upstream generation completes with an earlier request eligible for replay
- **AND** the downstream receive fails or is cancelled in the same wait cycle
- **WHEN** the proxy observes both completed awaitables
- **THEN** it detaches and propagates the downstream receive outcome before reconnecting
- **AND** it does not acquire a replacement upstream or resend the replay request
- **AND** it still releases the retiring upstream, leases, response-create gate, and replay state

#### Scenario: simultaneous disconnect prevents replay

- **GIVEN** an upstream generation completes with an earlier request eligible for replay
- **AND** the downstream receive yields `websocket.disconnect` in the same wait cycle
- **WHEN** the proxy observes both completed awaitables
- **THEN** it detaches the receive result, marks the downstream disconnected, and stops the session
- **AND** it does not acquire a replacement upstream or any additional stream lease, admission, or quota reservation
- **AND** it does not resend the replay request or emit an error to the disconnected client
- **AND** it still releases the retiring upstream, leases, response-create gate, and replay state

#### Scenario: receive ownership is cleaned up after an exceptional exit

- **GIVEN** a direct Responses WebSocket has an owned downstream receive operation
- **WHEN** the session exits because receiving or handing off raises an exception
- **THEN** the proxy detaches ownership before observing or awaiting the operation
- **AND** a secondary receiver exception is logged without replacing the primary failure
- **AND** upstream, account, admission, response-create gate, and pending cleanup still runs
- **AND** no downstream receive task remains orphaned
- **AND** no second receive operation runs concurrently for that connection
