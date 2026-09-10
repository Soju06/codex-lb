## MODIFIED Requirements

### Requirement: Packaged native egress is preferred only across a replay-safe boundary

When the fixed packaged `codex-lb-native-egress` executable is available, direct and account-routed Codex model-discovery, JSON/raw/multipart HTTP, Responses HTTP/SSE, and Responses or Live WebSocket calls MUST prefer it over the corresponding Python data-plane client. Python MUST retain ownership of account selection, route resolution, ordered proxy endpoint fallback, route metadata, and health classification, while each native command MUST target exactly one concrete direct or proxy endpoint. The worker MUST reuse one persistent helper generation and compatible reqwest HTTP/2 client pools across HTTP requests, and MUST multiplex concurrent HTTP and WebSocket operations without cross-delivering events. Native calls MUST preserve standard direct HTTP/HTTPS/SOCKS proxy environment resolution and `NO_PROXY` bypass behavior, and routed calls MUST use the resolved endpoint without consulting environment proxy variables. When the executable is absent or cannot start before dispatch, calls MUST retain their existing Python behavior without preventing startup. A non-idempotent request, WebSocket handshake, or WebSocket frame MUST NOT fall back to Python after its native command may have been dispatched. Helper failure MUST fail operations from that generation without replay and MAY be recovered only by starting a new generation for a later operation. A confirmed pre-dispatch routed connection failure MAY use the next endpoint under the existing route policy, while a TLS verification failure or ambiguous delivery MUST NOT gain new replay eligibility.

#### Scenario: Packaged direct request prefers native transport

- **GIVEN** the fixed native helper executable is available on the runtime path
- **WHEN** a direct model-discovery or Responses HTTP/SSE request starts
- **THEN** it is sent through the native helper
- **AND** no aiohttp request is sent for that successful attempt

#### Scenario: Compatible sequential requests reuse native pool

- **GIVEN** the fixed native helper is available
- **WHEN** compatible direct or routed requests complete sequentially in one worker
- **THEN** they use the same helper generation and compatible reqwest client pool
- **AND** the first response ending does not terminate the helper

#### Scenario: Concurrent native requests remain isolated

- **GIVEN** two direct or routed requests overlap in one helper generation
- **WHEN** their head, chunk, frame, acknowledgement, and terminal events interleave
- **THEN** each caller receives only events carrying its request identifier

#### Scenario: Missing helper preserves zero-configuration behavior

- **GIVEN** no native helper executable is available
- **WHEN** codex-lb starts and sends a supported direct or routed request or opens a supported WebSocket
- **THEN** startup succeeds
- **AND** the existing Python transport handles the operation

#### Scenario: Ambiguous native POST failure is not replayed

- **GIVEN** a direct or routed Responses POST command may have reached the helper
- **WHEN** the helper exits, its protocol fails, or its stream fails
- **THEN** that attempt fails through the existing Responses error path
- **AND** aiohttp and later route endpoints do not replay the POST

#### Scenario: Later request restarts a dead helper

- **GIVEN** a helper generation exited and its in-flight operations failed without replay
- **WHEN** a later new direct or routed operation begins
- **THEN** the worker may start a new helper generation for that new operation
- **AND** no operation from the failed generation is resubmitted

#### Scenario: Routed traffic retains existing transport

- **WHEN** an HTTP or WebSocket request uses a resolved upstream proxy route
- **THEN** Python selects one concrete endpoint and passes only that endpoint to the native helper
- **AND** route fallback and account-health provenance remain owned by Python

#### Scenario: Routed and WebSocket traffic retains existing transport

- **WHEN** a request uses a resolved upstream proxy route and the native helper is unavailable before dispatch
- **THEN** it retains the existing route-aware HTTP or WebSocket transport
- **AND** an available helper enters the separately covered routed native cutover

#### Scenario: Native direct request honors environment proxy routing

- **GIVEN** a standard HTTPS or SOCKS proxy environment variable applies to the Codex upstream URL
- **AND** `NO_PROXY` does not bypass that host and port
- **WHEN** a native direct request starts
- **THEN** the helper tunnels through the resolved environment proxy

#### Scenario: Direct WebSocket uses native helper

- **GIVEN** the fixed helper is available before connection dispatch
- **WHEN** codex-lb opens a direct or account-routed Responses or Live upstream WebSocket
- **THEN** the handshake and frames use the persistent native helper
- **AND** the Python WebSocket connector is not opened

#### Scenario: Native WebSocket failure is not replayed

- **GIVEN** a native WebSocket handshake or frame command may have reached the helper
- **WHEN** the helper reports a denial, transport failure, protocol failure, or exits
- **THEN** that connection fails through the existing WebSocket error contract
- **AND** codex-lb does not open a replacement Python connection or resend the frame

#### Scenario: Confirmed routed connect failure uses next endpoint

- **GIVEN** a routed native request has not reached upstream because connecting to its selected proxy endpoint failed
- **WHEN** the route has another endpoint and existing policy permits fallback
- **THEN** Python submits a new native command targeting the next endpoint
- **AND** route metadata records that endpoint and fallback use

#### Scenario: Routed TLS verification failure remains non-replayable

- **GIVEN** a non-idempotent routed native request fails TLS certificate verification
- **WHEN** another endpoint exists
- **THEN** the request fails on the selected endpoint
- **AND** neither the next endpoint nor aiohttp receives a replay
