# http-ingress-limits Specification

## Purpose
Incremental, budget-reusing bounds on raw HTTP request ingress, including encoded bodies before and after decompression and exact route-owned multipart exceptions.
## Requirements
### Requirement: Raw HTTP request ingress is bounded incrementally

The service MUST enforce the applicable request-body budget against actual raw bytes received for each guarded HTTP request. It MUST reject the request before exposing a chunk that would make the cumulative raw body exceed the budget, and it MUST NOT prebuffer the complete body solely to enforce this limit.

#### Scenario: Declared oversized body is rejected before downstream parsing

- **WHEN** a guarded HTTP request declares a valid `Content-Length` greater than its applicable budget
- **THEN** the service returns HTTP 413 without invoking downstream request-body parsing

#### Scenario: Chunked body crosses the budget

- **WHEN** a guarded HTTP request has no usable `Content-Length` and its received chunks cumulatively exceed the applicable budget
- **THEN** the service returns HTTP 413
- **AND** the chunk that crosses the budget is not exposed to downstream body parsing

#### Scenario: Exact-boundary body is accepted by the ingress guard

- **WHEN** a guarded HTTP request's actual raw body size equals its applicable budget
- **THEN** the raw ingress guard allows the complete body to continue downstream

#### Scenario: Client disconnect remains a disconnect

- **WHEN** the ASGI server reports `http.disconnect` while a guarded body is being received
- **THEN** the ingress guard propagates the disconnect without converting it into an HTTP 413 response

### Requirement: HTTP ingress reuses existing budgets

The service MUST use the fixed general HTTP body budget (`MAX_DECOMPRESSED_BODY_BYTES`, 32 MiB, in `app/core/ingress_limits.py`) as the general raw and decompressed HTTP request-body budget. When an owning route capability defines a larger fixed budget (the Responses budget `MAX_DECOMPRESSED_RESPONSES_BODY_BYTES`, 128 MiB, in the same module), the ingress guard MUST use that route budget. Neither budget is operator-configurable; the HTTP ingress guard MUST NOT add a setting or change the fixed values.

Route-specific budget and error-envelope selection MUST use the application-relative route path after removing any matching ASGI `root_path` prefix.

The generic guard MUST apply to requests solely because they declare `multipart/form-data`; the client-declared media type MUST NOT grant an exemption. An owning route capability MAY define an exact method/path-scoped authorization-before-read contract and dedicated bounded multipart parser. Only unencoded multipart requests to that exact operation, or requests marked by its outer content-encoding gate, MAY bypass generic admission. The gate MUST identify its operation independently of the declared media type, remove the encoding and mark the scope as handled without consuming the body, and the exception MUST NOT apply to any other operation.

#### Scenario: Another HTTP path uses the general budget

- **WHEN** a guarded request targets any other HTTP path
- **THEN** its raw and decompressed HTTP ingress budget is the fixed 32 MiB general budget

#### Scenario: Route-owned unencoded multipart uses dedicated admission

- **GIVEN** an exact operation has a capability-defined authorization-before-read contract and dedicated bounded multipart parser
- **WHEN** an unencoded request to that operation declares media type `multipart/form-data`
- **THEN** the generic raw whole-body guard does not preempt operation authorization or its dedicated parser limit

#### Scenario: Unrelated unencoded multipart remains guarded

- **WHEN** an unencoded request outside a route-owned multipart operation declares media type `multipart/form-data`
- **THEN** the service applies the generic raw-body budget
- **AND** the declared media type alone does not bypass admission

#### Scenario: Encoded multipart remains guarded

- **WHEN** a `multipart/form-data` request outside a route-owned multipart exception carries a `Content-Encoding` header
- **THEN** the service applies both the raw and decompressed budget checks

#### Scenario: Route-owned multipart admission can preserve authorization precedence

- **GIVEN** an exact operation has a capability-defined outer content-encoding gate, authorization-before-read contract, and dedicated bounded multipart parser
- **WHEN** an encoded request targets that operation, regardless of its declared media type
- **THEN** the generic raw and decompressed-body guards do not preempt operation authorization or its dedicated parser limit
- **AND** encoded multipart requests to all other operations remain guarded

#### Scenario: Mounted Responses route keeps its route-specific policy

- **GIVEN** the service is mounted under a non-empty ASGI `root_path`
- **WHEN** the request scope path includes that prefix and targets `/v1/responses` relative to the application
- **THEN** the service applies the Responses-specific ingress budget
- **AND** any ingress failure uses the OpenAI-compatible error envelope

### Requirement: Encoded HTTP bodies are bounded before and after decompression

For request bodies using `gzip`, `deflate`, `zstd`, `identity`, or supported stacked `Content-Encoding` values that remain under generic ingress admission, the service MUST enforce the applicable budget independently against the encoded raw body and every intermediate and final decoded representation. The service MUST remove stacked encodings in reverse header/application order. Unsupported encodings or malformed compressed bodies under generic admission MUST fail with HTTP 400. Exact route-owned exceptions MUST instead follow their owning capability's authorization and encoded-body rejection contract.

#### Scenario: Encoded raw body exceeds the budget

- **WHEN** a generic-guarded encoded request's raw bytes exceed the applicable budget before decompression
- **THEN** the service returns HTTP 413 before attempting to hold an unbounded encoded body

#### Scenario: Expanded body exceeds the budget

- **WHEN** a generic-guarded encoded request is within the raw budget but expands beyond the applicable decompressed budget
- **THEN** the service returns HTTP 413

#### Scenario: Supported stacked encoding remains compatible

- **WHEN** a generic-guarded request uses a valid supported stack of `gzip`, `deflate`, `zstd`, or `identity` encodings and both representations fit the budget
- **THEN** the service decodes the body in reverse header/application order, caps every intermediate representation, and continues request handling

#### Scenario: Invalid compression is rejected

- **WHEN** a generic-guarded request uses an unsupported content encoding or carries malformed compressed bytes
- **THEN** the service returns HTTP 400 without invoking route logic

### Requirement: HTTP ingress failures use the path-family error envelope

Ingress failures on `/v1/*`, `/backend-api/*`, `/api/codex/*`, and `/internal/bridge/*` MUST use an OpenAI-compatible error envelope with `type = invalid_request_error`. Equivalent paths MUST be classified after the existing outer path canonicalization. Other ingress paths MUST retain the dashboard-compatible error envelope. Oversized requests MUST use `code = payload_too_large`; malformed or unsupported compression MUST use `code = invalid_request_error` on OpenAI paths and `code = invalid_request` on other paths.

#### Scenario: OpenAI path rejects an oversized body

- **WHEN** a raw or decompressed request body on an OpenAI-compatible proxy path exceeds its budget
- **THEN** the service returns HTTP 413
- **AND** the response has OpenAI error `code = payload_too_large` and `type = invalid_request_error`

#### Scenario: OpenAI path rejects invalid compression

- **WHEN** a request on an OpenAI-compatible proxy path uses unsupported or malformed compression
- **THEN** the service returns HTTP 400
- **AND** the response has OpenAI error `code = invalid_request_error` and `type = invalid_request_error`

#### Scenario: Dashboard settings path rejects an oversized body

- **WHEN** a raw or decompressed request body on `/api/settings` exceeds its budget
- **THEN** the service returns HTTP 413
- **AND** the response has dashboard error `code = payload_too_large`

#### Scenario: Dashboard settings path rejects invalid compression

- **WHEN** a request on `/api/settings` uses unsupported or malformed compression
- **THEN** the service returns HTTP 400
- **AND** the response has dashboard error `code = invalid_request`

#### Scenario: Duplicated Codex alias is classified after canonicalization

- **WHEN** an ingress failure targets `/backend-api/codex/v1/responses/`
- **THEN** the service applies the same Responses budget and OpenAI-compatible envelope as `/backend-api/codex/responses/`

### Requirement: Ingress admission preserves endpoint authorization

The HTTP ingress guard MUST NOT authenticate callers or replace, bypass, or relocate existing dashboard, proxy API-key, ChatGPT-identity, or internal-bridge authorization. Requests that reach dependency resolution MUST continue through the endpoint's existing authorization path. Existing FastAPI parsing order remains unchanged, so ingress rejection or syntactically invalid typed bodies can fail before router-level authorization.

#### Scenario: Admitted unauthenticated request still reaches proxy authorization

- **WHEN** a syntactically valid under-limit request without required credentials targets an API-key-protected proxy route
- **THEN** the ingress guard allows normal routing to continue
- **AND** the existing proxy authorization rejects the request with its established authentication response

#### Scenario: Declared oversized generic-guarded request fails before authorization

- **WHEN** a guarded request outside a route-owned admission exception declares a body larger than its ingress budget
- **THEN** the service returns the deterministic ingress 413 without invoking router-level authorization

### Requirement: Multipart ingress exceptions are exact and route-owned

The generic raw HTTP body guard MUST exempt an unencoded `multipart/form-data` request only when the request is `POST` to `/api/accounts/import`, `/backend-api/transcribe`, `/v1/audio/transcriptions`, or `/v1/images/edits`, including their application-relative trailing-slash and mounted equivalents. Each exempt operation MUST apply its capability-defined authorization-before-read contract and dedicated bounded multipart parser.

For the same exact operations, an outer content-encoding gate MUST remove `Content-Encoding` and mark the copied request scope as route-owned without reading the body. The generic raw and decompression guards MUST honor that internal marker regardless of the declared media type so `identity` reaches dedicated multipart admission and non-identity encoding reaches the post-authorization rejection path.

The client-declared multipart media type MUST NOT exempt any other method or path. Every unrelated unencoded or encoded multipart request MUST remain under generic raw admission, and encoded requests MUST also retain generic decompressed-body admission.

#### Scenario: Exact unencoded multipart operation uses its dedicated parser

- **WHEN** an unencoded multipart request targets one of the four exact route-owned `POST` operations
- **THEN** generic admission does not preempt operation authorization
- **AND** the operation's dedicated multipart body limit remains authoritative

#### Scenario: Exact encoded operation preserves authorization precedence

- **WHEN** a request with `Content-Encoding` targets one of the four exact route-owned `POST` operations and declares either multipart or another media type
- **THEN** the outer gate marks the request without consuming its body
- **AND** generic raw or decompressed-body admission does not preempt operation authorization or its encoded-body contract

#### Scenario: Unrelated multipart media type grants no exemption

- **WHEN** an unencoded or encoded request outside the four exact route-owned `POST` operations declares `multipart/form-data`
- **THEN** the generic raw-body budget remains enforced
- **AND** an oversized declared body is rejected before downstream parsing

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

### Requirement: Zstd decoded output is bounded incrementally

The request-decompression middleware MUST consume zstd decoded output
incrementally. Before retaining each decoded chunk, it MUST enforce the
route-specific decompressed-body limit. The middleware MUST NOT decode an
entire zstd body through a one-shot output allocation before applying that
limit.

#### Scenario: Highly compressed zstd body exceeds decoded limit

- **WHEN** a zstd request body expands beyond the route-specific decoded-body
  limit
- **THEN** the middleware stops consuming decoded output once the next bounded
  chunk exceeds the remaining budget
- **AND** the request receives the existing body-too-large response
- **AND** the service remains available for subsequent requests

#### Scenario: Zstd body ends at the decoded limit

- **WHEN** a valid zstd request body expands to exactly the route-specific
  decoded-body limit
- **THEN** the middleware delivers the complete decoded body downstream

### Requirement: HTTP middleware relays responses in the request task

Every middleware in the HTTP middleware stack MUST be a pure ASGI middleware that invokes the downstream application in the same task and forwards response messages directly. The stack MUST NOT include Starlette `BaseHTTPMiddleware` (including `@app.middleware("http")` registrations). Response bodies forwarded on success paths MUST be byte-identical to the downstream application's output.

#### Scenario: Production middleware stack contains no BaseHTTPMiddleware

- **WHEN** the production application is constructed
- **THEN** no registered middleware entry is `starlette.middleware.base.BaseHTTPMiddleware`

#### Scenario: Streaming body is forwarded unchanged

- **WHEN** a route returns a streaming response through the middleware stack
- **THEN** the sequence of ASGI response messages, including headers, body bytes, and `more_body` flags, is identical to the sequence emitted without the middleware

#### Scenario: Mid-stream failure propagates without a synthetic terminator

- **WHEN** a response body generator raises after at least one body chunk has been sent
- **THEN** the exception propagates to the ASGI server
- **AND** the stack does not emit an additional `http.response.body` message with `more_body=false` before propagating

### Requirement: Keep-alive timers do not outlive lost connections

The server MUST release the per-connection keep-alive timer whenever an HTTP
connection is lost, regardless of whether the peer closed it cleanly or the
loss was reported with an error (connection reset, timeout, or any other
transport error). No per-connection server state (protocol, transport wrapper,
request cycle, or request scope) MAY remain reachable from the event loop
solely because a keep-alive timer is still armed for a connection that no
longer exists. Clean-close teardown and the timer's idle-close behavior on
intact connections MUST be unchanged, and the server MUST NOT close the
transport again on the error path.

#### Scenario: Peer resets an idle keep-alive connection after a response

- **WHEN** a client completes an HTTP/1.1 request on a keep-alive connection
  and then closes the connection abnormally so the server observes a
  connection-reset error rather than end-of-stream
- **THEN** the server cancels the connection's keep-alive timer immediately
- **AND** the connection's protocol state becomes garbage-collectable without
  waiting for the keep-alive window to elapse

#### Scenario: Clean close is unchanged

- **WHEN** a client completes a request and closes the connection cleanly
- **THEN** the server closes the transport and cancels the keep-alive timer as
  before
- **AND** the connection's protocol state becomes garbage-collectable

#### Scenario: Idle intact connection is still closed by the timer

- **WHEN** a keep-alive connection stays open and idle for the configured
  keep-alive window
- **THEN** the server closes the connection when the timer fires

### Requirement: Idle keep-alive window is bounded and configurable

The server MUST close an idle HTTP/1.1 keep-alive connection after a
configurable window. The default window MUST be 300 seconds. The window MUST
be configurable via the `--timeout-keep-alive` CLI flag and the
`UVICORN_TIMEOUT_KEEP_ALIVE` environment variable, with the CLI flag taking
precedence, and an invalid (non-integer) value MUST fail startup with a clear
error. The documented contract for the value is that it exceeds the largest
connection pool idle timeout of the clients and proxies the deployment serves
by a safety margin covering the network round-trip and timer scheduling
(practically `S >= 2C`; reqwest default: 90 seconds, so the 300-second default
leaves a 3.3x margin).

#### Scenario: Default keep-alive window

- **WHEN** the server starts without `--timeout-keep-alive` or
  `UVICORN_TIMEOUT_KEEP_ALIVE`
- **THEN** idle keep-alive connections are closed after 300 seconds

#### Scenario: Operator overrides the keep-alive window

- **WHEN** the operator starts the server with `--timeout-keep-alive <seconds>`
  or sets `UVICORN_TIMEOUT_KEEP_ALIVE=<seconds>`
- **THEN** idle keep-alive connections are closed after the configured window

#### Scenario: Invalid keep-alive window fails startup

- **WHEN** `UVICORN_TIMEOUT_KEEP_ALIVE` or `--timeout-keep-alive` is not an
  integer
- **THEN** startup fails with an error naming the flag and variable

