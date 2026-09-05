## ADDED Requirements

### Requirement: Packaged native egress is preferred only across a replay-safe boundary

When the fixed packaged `codex-lb-native-egress` executable is available, direct Codex model-discovery and direct Responses HTTP/SSE calls MUST prefer it over the Python HTTP client. Native direct calls MUST preserve standard HTTP/HTTPS/SOCKS proxy environment resolution and `NO_PROXY` bypass behavior. When the executable is absent or becomes unavailable before dispatch, the calls MUST retain their existing Python behavior without preventing startup. A non-idempotent Responses request MUST NOT fall back to Python after any native failure for which dispatch cannot be ruled out. Routed upstream-proxy and WebSocket calls MUST retain their existing transports until they receive separate parity and fallback coverage.

#### Scenario: Packaged direct request prefers native transport

- **GIVEN** the fixed native helper executable is available on the runtime path
- **WHEN** a direct model-discovery or Responses HTTP/SSE request starts
- **THEN** it is sent through the native helper
- **AND** no aiohttp request is sent for that successful attempt

#### Scenario: Missing helper preserves zero-configuration behavior

- **GIVEN** no native helper executable is available
- **WHEN** codex-lb starts and sends a direct supported request
- **THEN** startup succeeds
- **AND** the existing Python HTTP path handles the request

#### Scenario: Ambiguous native POST failure is not replayed

- **GIVEN** a direct Responses POST has started through the native helper
- **WHEN** the helper reports a protocol, timeout, transport, or body-stream failure that does not prove pre-dispatch unavailability
- **THEN** that attempt fails through the existing Responses error path
- **AND** aiohttp does not replay the POST

#### Scenario: Routed and WebSocket traffic retains existing transport

- **WHEN** a request uses a resolved upstream proxy route or upstream WebSocket transport
- **THEN** it does not enter this native HTTP cutover

#### Scenario: Native direct request honors environment proxy routing

- **GIVEN** a standard HTTPS or SOCKS proxy environment variable applies to the Codex upstream URL
- **AND** `NO_PROXY` does not bypass that host and port
- **WHEN** a native direct request starts
- **THEN** the helper tunnels through the resolved environment proxy
