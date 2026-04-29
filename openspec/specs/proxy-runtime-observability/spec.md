# proxy-runtime-observability Specification

## Purpose

See context docs for background.
## Requirements
### Requirement: Proxy 4xx/5xx responses are logged with error detail
When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. For local admission rejections, the log MUST also include the rejection stage or lane.

#### Scenario: Local admission rejection is logged
- **WHEN** the proxy rejects a request locally because a downstream or expensive-work admission lane is full
- **THEN** the console log includes the local response status, normalized error code and message
- **AND** it includes which admission lane or stage rejected the request

### Requirement: Proxy supports optional peer fallback before downstream output
The service MUST support an opt-in peer fallback path for proxy requests when the local `codex-lb` process is alive, accepted the request, and cannot complete the local proxy attempt before any downstream-visible output starts. This behavior MUST NOT be treated as process-down failover, health-check failover, load-balancer failover, or automatic replacement for a stopped local process.

#### Scenario: Local pre-output failure falls back to a peer
- **GIVEN** peer fallback is enabled and at least one eligible peer `codex-lb` instance is configured
- **AND** the local process is alive and handling the request
- **WHEN** the local proxy attempt fails or times out before sending response headers, body bytes, SSE events, websocket messages, or any other downstream-visible output
- **THEN** the service attempts the request through an eligible peer according to the configured peer fallback policy
- **AND** the client receives either the peer response or a stable fallback failure from the local service

#### Scenario: Local upstream rate limit falls back before output
- **GIVEN** peer fallback is enabled and at least one eligible peer `codex-lb` instance is configured
- **WHEN** all local account attempts produce a pre-output upstream rate-limit or quota failure such as `rate_limit_exceeded`, `usage_limit_reached`, `insufficient_quota`, `usage_not_included`, or `quota_exceeded`
- **THEN** the service attempts the request through an eligible peer according to the configured peer fallback policy
- **AND** it does not require the local failure to be rewritten to `no_accounts` before fallback is considered

#### Scenario: Process-down failover is out of scope
- **WHEN** the local `codex-lb` process is stopped, unreachable, or unable to accept the client connection
- **THEN** this peer fallback feature does not apply
- **AND** any process-down routing remains the responsibility of external supervisors, service discovery, or load balancers

#### Scenario: Disabled peer fallback preserves local behavior
- **GIVEN** peer fallback is disabled or no eligible peers are configured
- **WHEN** the local proxy attempt cannot complete before downstream output starts
- **THEN** the service returns the existing local failure behavior without attempting a peer fallback

### Requirement: Peer fallback must not occur after downstream-visible output starts
The service MUST attempt peer fallback only while it can still provide one coherent downstream response. Once the service has sent response headers, body bytes, an SSE event, a websocket message, or any other downstream-visible output for the local attempt, it MUST NOT attempt peer fallback for that request.

#### Scenario: HTTP headers prevent fallback
- **WHEN** the local proxy attempt has sent downstream HTTP response headers
- **AND** the local attempt later fails
- **THEN** the service does not attempt peer fallback for that request
- **AND** it completes or terminates the existing downstream response using the current transport behavior

#### Scenario: SSE event prevents fallback
- **WHEN** the local proxy attempt has sent at least one SSE event or body byte downstream
- **AND** the local attempt later fails
- **THEN** the service does not attempt peer fallback for that request
- **AND** it does not splice a peer stream into the existing downstream stream

### Requirement: Peer fallback prevents request loops
The service MUST mark peer fallback attempts so a request forwarded to a peer cannot be forwarded again through peer fallback. The service MUST detect inbound peer-forwarded requests and MUST NOT initiate another peer fallback for those requests.

#### Scenario: Peer-forwarded request fails locally on the peer
- **GIVEN** an inbound request contains the service's peer fallback marker
- **WHEN** that request cannot complete on the receiving peer before downstream-visible output starts
- **THEN** the receiving peer returns a local failure for that attempt
- **AND** it does not forward the request to another peer

#### Scenario: Existing peer marker is not trusted for repeated fallback
- **WHEN** a request already contains the service's peer fallback marker
- **THEN** the service treats the request as ineligible for peer fallback
- **AND** it records a no-fallback reason indicating loop prevention

### Requirement: Peer fallback initially supports HTTP and SSE proxy flows
Peer fallback MUST support non-streaming HTTP proxy responses and SSE proxy responses before adding other transports. The fallback attempt MUST preserve the original request method, path, query string, body, relevant proxy headers, streaming mode, and client-facing response semantics.

#### Scenario: Non-streaming HTTP request falls back before output
- **WHEN** an eligible non-streaming HTTP proxy request fails locally before downstream-visible output
- **THEN** the fallback peer receives an equivalent proxy request
- **AND** the local service relays the peer's final HTTP response to the original client

#### Scenario: SSE request falls back before first event
- **WHEN** an eligible SSE proxy request fails locally before response headers, body bytes, or SSE events are sent downstream
- **THEN** the fallback peer receives an equivalent streaming proxy request
- **AND** the local service relays the peer SSE stream to the original client without emitting local-attempt events first

### Requirement: Websocket peer fallback is deferred unless separately proven safe
The service MUST keep websocket peer fallback disabled or out of scope unless a later implementation explicitly proves that websocket handshake state, message ordering, timeout boundaries, request accounting, and duplicate-output prevention remain correct.

#### Scenario: Websocket request does not fallback by default
- **WHEN** a websocket proxy request fails locally after the downstream websocket connection is accepted or after any websocket message is sent downstream
- **THEN** the service does not attempt peer fallback for that websocket request
- **AND** it reports the failure using the websocket transport's existing error behavior
