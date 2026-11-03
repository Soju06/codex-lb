# outbound-http-clients delta

## MODIFIED Requirements

### Requirement: Native HTTP/2 startup profile matches measured Codex

Every persistent native HTTP client pool entry MUST use the measured Codex
initial HTTP/2 stream receive window, connection receive window, maximum frame
size, and maximum header-list size. It MUST NOT enable adaptive startup flow
control when that would replace the explicit profile. The maintained profile is
2,097,152 bytes for the stream receive window, 5,242,880 bytes for the
connection receive window, 16,384 bytes for maximum frame size, and 16,384
bytes for maximum header-list size.

Every persistent native HTTP client pool entry MUST configure TCP keepalive
and HTTP/2 keepalive PING frames on pooled connections (`tcp_keepalive`,
`http2_keep_alive_interval`, `http2_keep_alive_timeout`, and
`http2_keep_alive_while_idle`) with a 15-second TCP idle time before keepalive
probing, a 20-second HTTP/2 PING interval during active streams, and a 20-second
PING acknowledgement timeout, disabled while idle, to maintain peer connection
activity during quiet stream periods without altering idle connection pooling.

#### Scenario: Native helper starts a new HTTP/2 connection

- **WHEN** a direct or routed native HTTP request creates a fresh connection
- **THEN** its ordered initial SETTINGS and pre-request connection-control shape
  match the maintained direct-Codex profile
- **AND** the route choice does not select a different HTTP/2 profile

#### Scenario: Native HTTP client pool configures TCP and HTTP/2 keepalive probes

- **GIVEN** the packaged native egress helper is building an HTTP client
- **WHEN** `ClientPool::get` creates a `reqwest::Client`
- **THEN** TCP keepalive is enabled on upstream sockets
- **AND** HTTP/2 keepalive intervals and acknowledgement timeouts are enabled on the client builder for active streams while disabled on idle connections
