# http-ingress-limits Delta

## ADDED Requirements

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
