## MODIFIED Requirements

### Requirement: Packaged native egress is preferred only across a replay-safe boundary

When the fixed packaged `codex-lb-native-egress` executable is available, direct Codex model-discovery, direct Responses HTTP/SSE calls, and direct upstream Responses or Live WebSockets MUST prefer it over the corresponding Python client. The worker MUST reuse one persistent helper generation and compatible reqwest HTTP/2 client pools across HTTP requests, and MUST multiplex concurrent HTTP and WebSocket operations without cross-delivering events. Native direct calls MUST preserve standard HTTP/HTTPS/SOCKS proxy environment resolution and `NO_PROXY` bypass behavior. When the executable is absent or cannot start before dispatch, calls MUST retain their existing Python behavior without preventing startup. A non-idempotent Responses request, WebSocket handshake, or WebSocket frame MUST NOT fall back to Python after its native command may have been dispatched. Helper failure MUST fail operations from that generation without replay and MAY be recovered only by starting a new generation for a later operation. Account-routed upstream-proxy HTTP and WebSocket calls MUST retain their existing route-aware transports.

#### Scenario: Packaged direct request prefers native transport

- **GIVEN** the fixed native helper executable is available on the runtime path
- **WHEN** a direct model-discovery or Responses HTTP/SSE request starts
- **THEN** it is sent through the native helper
- **AND** no aiohttp request is sent for that successful attempt

#### Scenario: Compatible sequential requests reuse native pool

- **GIVEN** the fixed native helper is available
- **WHEN** compatible direct requests complete sequentially in one worker
- **THEN** they use the same helper generation and compatible reqwest client pool
- **AND** the first response ending does not terminate the helper

#### Scenario: Concurrent native requests remain isolated

- **GIVEN** two direct requests overlap in one helper generation
- **WHEN** their head, chunk, frame, acknowledgement, and terminal events interleave
- **THEN** each caller receives only events carrying its request identifier

#### Scenario: Missing helper preserves zero-configuration behavior

- **GIVEN** no native helper executable is available
- **WHEN** codex-lb starts and sends a direct supported request or opens a supported WebSocket
- **THEN** startup succeeds
- **AND** the existing Python transport handles the operation

#### Scenario: Ambiguous native POST failure is not replayed

- **GIVEN** a direct Responses POST command may have reached the helper
- **WHEN** the helper exits, its protocol fails, or its stream fails
- **THEN** that attempt fails through the existing Responses error path
- **AND** aiohttp does not replay the POST

#### Scenario: Later request restarts a dead helper

- **GIVEN** a helper generation exited and its in-flight operations failed without replay
- **WHEN** a later new direct operation begins
- **THEN** the worker may start a new helper generation for that new operation
- **AND** no operation from the failed generation is resubmitted

#### Scenario: Routed traffic retains existing transport

- **WHEN** an HTTP or WebSocket request uses a resolved upstream proxy route
- **THEN** it does not enter the native direct cutover
- **AND** route failover and account-health provenance are unchanged

#### Scenario: Routed and WebSocket traffic retains existing transport

- **WHEN** a request uses a resolved upstream proxy route
- **THEN** it retains the existing route-aware HTTP or WebSocket transport
- **AND** a direct WebSocket may enter the separately covered native cutover

#### Scenario: Native direct request honors environment proxy routing

- **GIVEN** a standard HTTPS or SOCKS proxy environment variable applies to the Codex upstream URL
- **AND** `NO_PROXY` does not bypass that host and port
- **WHEN** a native direct request starts
- **THEN** the helper tunnels through the resolved environment proxy

#### Scenario: Direct WebSocket uses native helper

- **GIVEN** the fixed helper is available before connection dispatch
- **WHEN** codex-lb opens a direct Responses or Live upstream WebSocket
- **THEN** the handshake and frames use the persistent native helper
- **AND** the Python WebSocket connector is not opened

#### Scenario: Native WebSocket failure is not replayed

- **GIVEN** a native WebSocket handshake or frame command may have reached the helper
- **WHEN** the helper reports a denial, transport failure, protocol failure, or exits
- **THEN** that connection fails through the existing WebSocket error contract
- **AND** codex-lb does not open a replacement Python connection or resend the frame
