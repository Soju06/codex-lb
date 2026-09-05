## ADDED Requirements

### Requirement: Steering continuations retain owned WebSocket lifecycles
The proxy SHALL accept valid response.steer events on an active subscription Responses WebSocket for an owned Astra response and SHALL forward them on that response's existing upstream connection/account. Steering events SHALL contain only type, previous_response_id and a nonempty supported user input. Accepted, pending, failed, and automatically created continuation responses SHALL remain correlated to the originating request and API key. Each continuation SHALL receive admission and usage accounting and SHALL settle once on its own terminal event. Each additional queued steer SHALL extend the same successor reservation before upstream dispatch; rejection SHALL release only that submission's unapplied reservation increment without charging or settling the successor twice. If this refund fails, the proxy SHALL retain the existing reservation for normal terminal reconciliation without terminating unrelated in-flight responses; failed admission extensions SHALL still reject the steer. A steered incomplete response SHALL not be treated as an unhealthy upstream account. Automatic continuations SHALL not bind to unrelated queued response.create requests. Completed request-state retention for post-completion steering SHALL be limited to Astra responses; a subsequent successful non-Astra response SHALL clear the retained steering parent.

#### Scenario: Steering creates an automatic successor
- **GIVEN** an owned Astra response and accepted steering
- **WHEN** the original response ends with incomplete reason steered and upstream automatically creates a successor
- **THEN** the successor retains the original account and policy ownership and its usage is recorded exactly once

#### Scenario: Ordinary responses do not retain steering request bodies
- **GIVEN** a WebSocket previously completed an Astra response
- **WHEN** a non-Astra response reaches a successful completion boundary
- **THEN** the connection clears the retained steering parent without retaining the completed non-Astra request body
- **AND** successful Astra responses remain available for owned post-completion steering

#### Scenario: Steering waits for a tool result
- **WHEN** upstream reports response.steer.pending with required tool input
- **THEN** the proxy preserves that notification and allows the matching explicit anchored response.create to continue on the same connection without replaying the steer

#### Scenario: Required tool input arrives before pending notification
- **GIVEN** steering has been accepted and the original response has completed with a tool call
- **WHEN** the client sends the matching anchored tool result before response.steer.pending arrives
- **THEN** the explicit continuation is forwarded on the same connection and owns its terminal accounting

#### Scenario: Multiple accepted steers share one automatic successor
- **GIVEN** multiple steering inputs are accepted for the same response
- **WHEN** upstream creates their automatic successor
- **THEN** the proxy owns one continuation lifecycle and one reservation for that successor

#### Scenario: Additional steering input exceeds the remaining quota
- **GIVEN** an automatic successor already has a reservation for an earlier steer
- **WHEN** a later steer would exceed an applicable API-key quota after extending that reservation
- **THEN** the later steer is rejected before upstream dispatch
- **AND** the earlier submission and reservation remain valid

#### Scenario: A rejected steer releases its reservation increment
- **GIVEN** several admitted steering submissions share one successor reservation
- **WHEN** upstream rejects one submission before applying it
- **THEN** its unapplied reservation increment is released while the remaining submissions retain their reserved usage
- **AND** final successor usage is settled once against the remaining reservation

#### Scenario: A failed refund preserves the connection and settlement
- **GIVEN** several steering submissions share a reservation and the quota window changes before one is rejected
- **WHEN** refunding that rejected submission fails
- **THEN** the proxy keeps the reservation ledger intact for terminal reconciliation
- **AND** other in-flight responses and the remaining steering continuation can complete on the connection

#### Scenario: A new successor after rejected steering reserves input once
- **GIVEN** an earlier steering continuation was rejected and its parent retains migrated steering configuration
- **WHEN** a new steering submission creates a new successor reservation
- **THEN** that submission is reserved once without immediately extending the new reservation for the same input

#### Scenario: Disconnect does not replay accepted steering
- **GIVEN** steering is accepted on a connection
- **WHEN** that connection closes before application completes
- **THEN** all locally owned reservations/tasks are finalized and the proxy does not replay the queued steering on another account or connection

#### Scenario: Failed or malformed steering does not corrupt other work
- **WHEN** a steering request is invalid, unknown, or upstream reports response.steer.failed
- **THEN** the failure is returned without assigning its lifecycle to an unrelated queued create or charging usage twice

#### Scenario: A late successor does not consume unrelated admission
- **GIVEN** a known steering continuation no longer has a pending request state after expiry or during explicit replacement
- **WHEN** its late response.created event names the original parent
- **THEN** the event is not assigned through the generic create queue
- **AND** unrelated request identity, usage ownership and create admission remain unchanged

#### Scenario: A suppressed successor's anonymous error does not settle unrelated work
- **GIVEN** a late automatic successor was suppressed because its continuation was no longer pending
- **AND** an unrelated visible request already has a response id
- **WHEN** an ID-less top-level error arrives
- **THEN** the error is not assigned to the unrelated request
- **AND** that unrelated request still owns its created event

#### Scenario: A live request owns anonymous errors before a successor tombstone
- **GIVEN** a suppressed late successor and a visible request, including one that already has a response id
- **WHEN** an ID-less top-level error arrives
- **THEN** the visible request owns that error
- **AND** the suppressed successor lifecycle remains available for a later unmatched terminal

#### Scenario: An already-sent explicit create owns the next created event
- **GIVEN** an explicit response.create for parent r1 is pending and a steering placeholder also exists for r1
- **WHEN** upstream emits response.created naming r1
- **THEN** the explicit create receives that response id
- **AND** the steering placeholder is not bound to it

#### Scenario: A rejected submission releases its queued byte budget
- **GIVEN** multiple steering submissions share one continuation
- **WHEN** upstream rejects one submission
- **THEN** the queued byte count excludes the rejected submission
- **AND** a subsequent valid submission can use the released capacity

#### Scenario: Empty structured text is rejected before steering admission
- **WHEN** a steering user message contains an input_text part with an empty text string
- **THEN** the proxy returns invalid_input before creating or reserving a continuation

#### Scenario: Explicit continuation prepares before releasing the placeholder
- **GIVEN** a steering continuation has a placeholder request state
- **WHEN** the client sends the matching explicit response.create
- **THEN** the proxy prepares and registers that request before removing or releasing the placeholder
- **AND** a failed prepare or admission leaves the placeholder in place

#### Scenario: Placeholder refund failure does not abort an explicit continuation
- **GIVEN** an explicit continuation has been prepared and registered
- **WHEN** releasing the replaced placeholder reservation fails
- **THEN** the continuation remains pending and the socket continues

#### Scenario: Apply-patch output is required for explicit continuation
- **GIVEN** a completed steered response with a synchronous apply_patch_call
- **WHEN** the client sends the matching apply_patch_call_output before response.steer.pending
- **THEN** the explicit continuation is forwarded on the same connection

#### Scenario: Upstream steering failures are sanitized before forwarding
- **WHEN** upstream sends response.steer.failed with a malformed or structured error.param
- **THEN** the forwarded client payload omits the non-public parameter value

