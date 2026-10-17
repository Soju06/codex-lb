## ADDED Requirements

### Requirement: Direct WebSocket dispatch respects account availability

Before forwarding each new response.create frame on a direct WebSocket, the service MUST check the cluster-coherent routing-unavailable state after admission and capacity waits. If its socket account is unavailable, it MUST reject that unsent request, release its API-key and response-create reservations, and record the failure. It MUST NOT dispatch it upstream, select another account, discard its context anchors, close the shared socket, or interrupt already dispatched responses. A request with previous_response_id MUST receive previous_response_owner_unavailable; other requests MUST receive preferred_account_unavailable. The check MUST NOT add a per-frame database read. After reactivation, a valid continuation MUST be able to reuse the same open upstream and unchanged previous_response_id without full history retransmission.

#### Scenario: Pause blocks the next continuation
- **GIVEN** a direct WebSocket has completed a response on account A
- **AND** A is subsequently paused
- **WHEN** the client submits a delta referring to that response
- **THEN** only the new request fails with previous_response_owner_unavailable
- **AND** the frame is not sent upstream and account A's socket and anchors remain intact

#### Scenario: Reactivation resumes without replay
- **GIVEN** a continuation was rejected because account A was paused
- **WHEN** A is reactivated and the client resubmits that continuation on the same socket
- **THEN** the original upstream receives its unchanged parent and delta only
- **AND** no alternate account is selected

#### Scenario: Pause during admission
- **GIVEN** a new response-create waits for capacity on an open socket
- **WHEN** its account becomes unavailable before dispatch
- **THEN** the request is rejected and all of its reservations are released

#### Scenario: Existing response drains while new work is rejected
- **GIVEN** account A already has a response streaming on the socket
- **WHEN** A is paused and another response-create arrives
- **THEN** only the unsent request is rejected and the existing response can complete

#### Scenario: Fresh frames are also blocked
- **GIVEN** an open socket belongs to unavailable account A
- **WHEN** a response-create without previous_response_id arrives
- **THEN** it fails with preferred_account_unavailable without dispatch or account switching
