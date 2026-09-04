## ADDED Requirements

### Requirement: Async tool results remain pending across continuations
The proxy SHALL preserve async true on function/custom tool definitions and corresponding emitted call items. It SHALL allow a subsequent response to omit an async call result while retaining that call identity for later output. It SHALL NOT synthesize an interrupted-tool result for a known asynchronous call. Matching actual outputs SHALL complete the corresponding pending async call without consuming unrelated pending calls.

#### Scenario: Async work spans an intervening turn
- **GIVEN** an Astra response emits async function call call_a
- **WHEN** an anchored follow-up contains a new user message without call_a output
- **THEN** no synthetic output for call_a is forwarded
- **AND** a later actual call_a output can be forwarded unchanged

#### Scenario: Async and synchronous calls coexist
- **GIVEN** the previous response contains async call_a and interrupted synchronous call_b
- **WHEN** an anchored follow-up omits both outputs
- **THEN** only call_b receives the existing synthetic interrupted output

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

#### Scenario: A rejected submission releases its queued byte budget
- **GIVEN** multiple steering submissions share one continuation
- **WHEN** upstream rejects one submission
- **THEN** the queued byte count excludes the rejected submission
- **AND** a subsequent valid submission can use the released capacity

#### Scenario: Empty structured text is rejected before steering admission
- **WHEN** a steering user message contains an input_text part with an empty text string
- **THEN** the proxy returns invalid_input before creating or reserving a continuation

### Requirement: Astra configuration updates preserve compatible history
For subscription-backed Astra, the proxy SHALL preserve supported configuration_update input items and normalize client-plane ultra to max. It SHALL reject unsupported reasoning values and invalid adjacent updates before upstream work. Histories with configuration updates SHALL reject automatic compaction, automatic truncation, and the standalone compact endpoint. Normal request-level reasoning SHALL remain unchanged by a valid input update. Explicit terminal compaction_trigger items combined with configuration updates SHALL remain on the Responses endpoint instead of being converted to a standalone compact request. Astra-specific model restrictions SHALL NOT be applied to externally configured model sources sharing the same model ID.

#### Scenario: A valid update preserves the request cache prefix
- **WHEN** a request has request-level low reasoning and a high configuration_update between conversation messages
- **THEN** the forwarded request retains low at request level and high in that input item

#### Scenario: Explicit compaction retains configuration history
- **WHEN** a subscription Astra Responses request contains configuration updates and one terminal compaction_trigger
- **THEN** the proxy forwards the updates and trigger on the Responses path without calling the standalone compact endpoint

#### Scenario: A source with the same model name owns its model contract
- **GIVEN** a request routes to an externally configured model source named gpt-6-astra
- **WHEN** that source supports its own reasoning levels, logprobs or configuration update schema
- **THEN** subscription-specific Astra validation does not override that contract
- **AND** API-key reasoning policy remains enforced before source forwarding

#### Scenario: Source WebSocket fallback precedes subscription validation
- **GIVEN** a WebSocket response.create routes to an externally configured model source named gpt-6-astra
- **WHEN** the request carries source-specific controls that subscription Astra would reject
- **THEN** the proxy emits model_source_requires_http_transport before applying subscription-specific validation
- **AND** no subscription account is selected or contacted

#### Scenario: Configuration updates cannot use standalone compaction
- **WHEN** a compact request contains a configuration_update item
- **THEN** the proxy returns a compatible invalid-request error before upstream work

## MODIFIED Requirements

### Requirement: Interrupted tool calls receive synthetic outputs on anchored follow-ups
The service MUST track tool-call items completed by a streamed response that may still require a tool output — `function_call`, `custom_tool_call`, and `apply_patch_call` — together with each call's item type. Known asynchronous function/custom calls SHALL be excluded from synthetic interruption repair until actual outputs arrive; their omission on intervening turns is valid. For the remaining synchronous calls, when a follow-up `response.create` anchors on that completed response via `previous_response_id` and its input omits an output item for a tracked call id, the service MUST prepend a synthetic interrupted output item whose type matches the originating call type (`function_call` -> `function_call_output`, `custom_tool_call` -> `custom_tool_call_output`, `apply_patch_call` -> `apply_patch_call_output`) before forwarding the request upstream. This applies to the direct WebSocket route and to the HTTP responses bridge session path.

#### Scenario: interrupted custom tool call on the WebSocket route
- **GIVEN** a WebSocket `response.create` turn completes with a `custom_tool_call` item whose output was never sent (the turn was interrupted)
- **WHEN** the next `response.create` on the same session references that response via `previous_response_id` without a `custom_tool_call_output` for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input
- **AND** the follow-up does not fail with an upstream `No tool output found for custom tool call` error

#### Scenario: interrupted custom tool call on the HTTP bridge
- **GIVEN** an HTTP bridge session completes a response containing a `custom_tool_call` item whose output was never sent
- **WHEN** the next bridge request anchors on that response id (client-sent or proxy-injected `previous_response_id`) without an output item for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input

#### Scenario: interrupted function call keeps existing output type
- **WHEN** the pending tool call recorded from the previous response is a `function_call`
- **THEN** the synthetic interrupted output item is a `function_call_output` (existing behavior preserved)

#### Scenario: follow-up that carries the tool output is not modified
- **WHEN** the anchored follow-up input already contains a `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` item for a pending call id
- **THEN** the service does not inject a synthetic output for that call id

#### Scenario: injected bridge outputs stay subject to the request size guard
- **GIVEN** an HTTP bridge follow-up whose serialized `response.create` is close to the upstream byte limit
- **WHEN** synthetic interrupted outputs are injected
- **THEN** the service prepares the upstream request from the injected payload so the `response.create` slim/size guard runs against the bytes actually sent upstream
- **AND** an over-limit injected request is rejected locally with `payload_too_large` instead of being forwarded upstream

#### Scenario: stored input context reflects the injected upstream input
- **WHEN** an HTTP bridge follow-up gains synthetic interrupted outputs
- **THEN** the input item count, input fingerprint, and request usage budget recorded for the request are computed from the injected upstream-shaped input, so later full-resend/anchor comparisons on the same bridge session match what upstream actually stored

#### Scenario: unfingerprinted input turns keep the WebSocket continuity anchor
- **GIVEN** a WebSocket turn whose request input yields no prefix fingerprint (a string input — normalized to a single user message at request validation — or an empty input list)
- **WHEN** the response completes with pending tool-call items
- **THEN** the continuity state still records the completed response id and the pending tool-call metadata for all tracked call types, clearing only the prefix count/fingerprint pair
- **AND** a follow-up that anchors on that response id receives the synthetic interrupted outputs instead of leaking the upstream missing-tool-output 400

#### Scenario: local previous-response recovery retry keeps injected outputs
- **GIVEN** an HTTP bridge submit whose payload gained synthetic interrupted outputs and which fails before yielding with a previous-response continuity error
- **WHEN** the local recovery path re-prepares the anchored retry request
- **THEN** the synthetic interrupted outputs are re-injected from the failed session's pending tool-call state, so the recovered submit does not reintroduce the upstream missing-tool-output failure

#### Scenario: replayed apply_patch prefix is trimmed on anchored bridge follow-ups
- **GIVEN** an HTTP bridge follow-up that anchors via `previous_response_id` and replays a prior `apply_patch_call` item (marked as response output) followed by its `apply_patch_call_output`
- **WHEN** the bridge trims the previous-response prefix already covered by the anchor
- **THEN** `apply_patch_call` and `apply_patch_call_output` items are recognized by the trim exactly like the `function_call` and `custom_tool_call` variants, matching the WebSocket route's replay trim

#### Scenario: owner-forward failover recovery injects from local session state when available
- **GIVEN** a multi-instance bridge where an anchored follow-up is forwarded to the remote owner instance and the relay fails before yielding any bytes
- **WHEN** the local instance recovers by rebinding a local bridge session and resubmitting the anchored request
- **THEN** the service injects synthetic interrupted outputs when the rebound local session still holds the pending tool-call state for the anchored response id (for example after ownership flapped back to this instance)

#### Scenario: owner-forward failover recovery without local pending state is a known bounded gap
- **GIVEN** the same owner-forward failure, where the pending tool-call metadata exists only in the remote owner instance's memory (the durable bridge store does not persist pending call ids)
- **WHEN** the local recovery rebinds a fresh session that has no pending tool-call state
- **THEN** the anchored recovery request is resubmitted unmodified, without fabricated tool outputs (matching pre-injection behavior)
- **AND** if upstream rejects it with a missing-tool-output error, the extended classifier masks it as a retryable continuity failure instead of surfacing the raw upstream 400
