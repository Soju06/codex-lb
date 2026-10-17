## MODIFIED Requirements

### Requirement: Packaged native egress is preferred only across a replay-safe boundary

When the fixed packaged `codex-lb-native-egress` executable is available, direct Codex model-discovery and direct Responses HTTP/SSE calls MUST prefer it over the Python HTTP client. The worker MUST reuse one persistent helper generation and compatible reqwest HTTP/2 client pools across requests, and MUST multiplex concurrent requests without cross-delivering events. Native direct calls MUST preserve standard HTTP/HTTPS/SOCKS proxy environment resolution and `NO_PROXY` bypass behavior. When the executable is absent or cannot start before dispatch, calls MUST retain their existing Python behavior without preventing startup. A non-idempotent Responses request MUST NOT fall back to Python after its native command may have been dispatched. Helper failure MUST fail requests from that generation without replay and MAY be recovered only by starting a new generation for a later request. Routed upstream-proxy and WebSocket calls MUST retain their existing transports until they receive separate parity and fallback coverage.

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
- **WHEN** their head, chunk, and terminal events interleave
- **THEN** each caller receives only events carrying its request identifier

#### Scenario: Missing helper preserves zero-configuration behavior

- **GIVEN** no native helper executable is available
- **WHEN** codex-lb starts and sends a direct supported request
- **THEN** startup succeeds
- **AND** the existing Python HTTP path handles the request

#### Scenario: Ambiguous native POST failure is not replayed

- **GIVEN** a direct Responses POST command may have reached the helper
- **WHEN** the helper exits, its protocol fails, or its stream fails
- **THEN** that attempt fails through the existing Responses error path
- **AND** aiohttp does not replay the POST

#### Scenario: Later request restarts a dead helper

- **GIVEN** a helper generation exited and its in-flight requests failed without replay
- **WHEN** a later new direct request begins
- **THEN** the worker may start a new helper generation for that new request
- **AND** no request from the failed generation is resubmitted

#### Scenario: Routed and WebSocket traffic retains existing transport

- **WHEN** a request uses a resolved upstream proxy route or upstream WebSocket transport
- **THEN** it does not enter this native HTTP cutover

#### Scenario: Native direct request honors environment proxy routing

- **GIVEN** a standard HTTPS or SOCKS proxy environment variable applies to the Codex upstream URL
- **AND** `NO_PROXY` does not bypass that host and port
- **WHEN** a native direct request starts
- **THEN** the helper tunnels through the resolved environment proxy
