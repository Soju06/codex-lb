# http-ingress-limits Delta

## ADDED Requirements

### Requirement: Non-WebSocket upgrade offers are served as plain HTTP/1.1

The server MUST serve a valid HTTP/1.1 request that offers a non-WebSocket
protocol switch (`Connection: Upgrade` with an `Upgrade` token other than
`websocket`, such as `h2c`) as a normal HTTP/1.1 request. The complete request
body MUST reach the application whether it arrives coalesced with the headers
or in later TCP segments, and the offer MUST NOT cause the request or the
connection to be rejected. The declined offer's hop-by-hop headers (`Upgrade`,
`HTTP2-Settings`, and their `Connection` tokens) MUST NOT be exposed to the
application. Genuine WebSocket upgrade requests MUST keep completing the
protocol switch.

#### Scenario: h2c offer with the body coalesced with the headers

- **WHEN** a client sends an HTTP/1.1 POST carrying `Connection: Upgrade,
  HTTP2-Settings`, `Upgrade: h2c`, and `HTTP2-Settings` headers with the body
  in the same TCP segment as the headers
- **THEN** the application receives the complete request body
- **AND** the application does not observe the `Upgrade`, `HTTP2-Settings`, or
  `Connection: Upgrade` headers

#### Scenario: h2c offer with the body in a separate segment

- **WHEN** the same request arrives with the headers and the body written as
  separate TCP segments
- **THEN** the application receives the complete request body
- **AND** the server does not answer `400 Bad Request` at the protocol layer

#### Scenario: Repeated Connection fields do not hide the offer

- **WHEN** the h2c offer arrives with `Connection: Upgrade, HTTP2-Settings`
  followed by a second `Connection: keep-alive` field
- **THEN** the application receives the complete request body
- **AND** the surviving `Connection` tokens (such as `keep-alive`) are
  preserved while the upgrade tokens are removed

#### Scenario: Connection stays usable after a declined offer

- **WHEN** a request with a declined h2c offer completes on a keep-alive
  connection
- **THEN** a subsequent plain HTTP/1.1 request on the same connection is
  served normally

#### Scenario: Pipelined offers in one segment do not exhaust the server

- **WHEN** a single TCP segment pipelines many upgrade-offering requests
- **THEN** every request is served as plain HTTP/1.1 without the per-offer
  replay growing the call stack or aborting the connection

#### Scenario: WebSocket upgrades still switch protocols

- **WHEN** a client requests a WebSocket upgrade (`Upgrade: websocket`)
- **THEN** the protocol switch completes and WebSocket messages flow
