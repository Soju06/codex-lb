## MODIFIED Requirements

### Requirement: Responses input image bridge eligibility

For `/v1/responses` and `/backend-api/codex/responses`, including equivalent trailing-slash routes and collected or streamed responses, an otherwise bridge-eligible request containing inline `data:` input images within the WebSocket payload budget MUST use the ordinary reusable HTTP responses bridge. Images in retained history, top-level input items, nested message content or tool output content MUST NOT alone disable bridge eligibility or change affinity. The service MUST preserve their image content on the upstream wire. Existing route eligibility MUST remain unchanged: backend `/backend-api/codex/responses` requests with `stream: false` retain their non-bridge collection path, even when image-free.

Unsupported uploaded-image references MUST still be rejected before bridge dispatch. External HTTP(S) image URLs at any input nesting depth and over-budget image requests MUST retain their non-bridge HTTP routing. Image-generation tools, explicit HTTP controls, disabled bridge, outage fallback and gateway-safe-mode constraints MUST retain their existing behavior. These request-scoped exclusions MUST NOT disable later eligible bridge requests. Compact requests MUST retain their existing transport and validation behavior.

An upstream terminal image error received before `response.created` MUST settle the owning request without waiting for an acknowledgement timeout, preserve the client-facing error contract, and release its pending slot, response-create gate and API-key reservation. Cancellation MUST release local request resources and isolate ambiguous upstream work using the ordinary bridge retirement rules. A subsequent eligible request MUST be able to complete without consuming stale image-turn events or inheriting an unsettled reservation.

An image request that receives no response-lifecycle event MUST fail at the existing response-create acknowledgement deadline without pre-created replay of its image payload. The bridge MUST retire the ambiguous session and settle the slot, gate and API-key reservation. Silence MUST NOT be reported as a validated invalid-image error.

#### Scenario: Inline image retained across later text turns

- **GIVEN** a text request has warmed an eligible bridge session
- **WHEN** the same affinity sends an inline-image turn and then a text turn retaining that image in history
- **THEN** all three requests use the same physical upstream WebSocket session
- **AND** both image-bearing upstream payloads preserve the image bytes

#### Scenario: Nested inline image remains eligible

- **GIVEN** an otherwise bridge-eligible below-budget request
- **WHEN** an inline `data:` image is nested inside tool output content
- **THEN** the ordinary bridge carries that image unchanged

#### Scenario: Unsafe image shapes remain excluded

- **WHEN** an image request contains an external HTTP(S) URL anywhere in input or exceeds the WebSocket payload budget
- **THEN** the service bypasses the bridge and selects upstream HTTP
- **AND** an image-generation request still bypasses the bridge

#### Scenario: Invalid image rejected before acknowledgement

- **GIVEN** an image turn is pending on a reusable bridge session
- **WHEN** upstream sends a terminal invalid-image error before `response.created`
- **THEN** the request promptly returns the existing client-facing error
- **AND** its slot, gate and API-key reservation are settled
- **AND** a later eligible text request can complete

#### Scenario: Silent image upstream is not replayed

- **GIVEN** an inline-image request has been sent on a reusable bridge session
- **WHEN** no response-lifecycle event arrives before the existing acknowledgement deadline
- **THEN** the client receives an upstream timeout without resending the image
- **AND** the session is retired and its slot, gate and reservation are settled
- **AND** a later eligible text request can complete on a fresh session

#### Scenario: Cancellation isolates ambiguous image work

- **GIVEN** an image request has been sent upstream but has not completed
- **WHEN** the downstream request is cancelled
- **THEN** its slot, gate and reservation are released and the ambiguous session is retired
- **AND** a later eligible request completes on a fresh session without receiving the cancelled turn's events
