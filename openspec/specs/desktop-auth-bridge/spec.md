# Desktop Auth Bridge Specification

## Purpose

Define the observable contract of the opt-in loopback authentication relay.

## Requirements

### Requirement: The desktop bridge is an opt-in loopback relay

The desktop bridge MUST run only when an operator starts it explicitly and MUST
NOT add a `CODEX_LB_*` setting, environment variable, database row, or dashboard
control. It MUST listen only on the literal address `127.0.0.1` and MUST forward
only to `http://127.0.0.1:<lb_port>`; no request field or option may select
another destination. The listen and LB ports MUST each be integers from 1 to
65535 and MUST differ. Before serving, the bridge MUST validate its
configuration and both credential files and MUST fail with an actionable
message that contains no credential value or filesystem path.

#### Scenario: invalid ports fail at startup

- **WHEN** the bridge is configured with a port outside 1..65535 or with equal listen and LB ports
- **THEN** startup fails before binding a socket
- **AND** the error message contains no path or credential value

#### Scenario: unusable credentials fail at startup

- **WHEN** the Codex `auth.json` or LB token file is missing, not private, or invalid at startup
- **THEN** startup fails with a generic actionable message
- **AND** the message contains neither the file path nor any token

### Requirement: Local credentials are read from private files on every request

The bridge MUST read `<codex_home>/auth.json` and the LB token file on every
request, so rotations take effect without restart, and MUST NOT write either
file. Each file MUST be a regular file opened without following a final
symlink where the platform supports it, and its size MUST be bounded. On POSIX
the file MUST be owned by the current user and MUST NOT grant any group or
other permission bit. `auth.json` MUST be a JSON object whose
`tokens.access_token` is a non-empty visible-ASCII string; its `auth_mode` MUST
be absent or `chatgpt`, and its `OPENAI_API_KEY` MUST be absent, null, or empty.
The LB token file MUST contain exactly one non-empty line of visible ASCII
(0x21-0x7E) with at most one trailing line terminator. When either credential
is missing, partial, unreadable, or invalid at request time, the bridge MUST
answer a generic 503 without contacting codex-lb.

#### Scenario: rotated token is used without restart

- **GIVEN** a running bridge
- **WHEN** the LB token file is replaced with a new private token
- **THEN** the next forwarded request carries the new LB token

#### Scenario: group-readable credential is refused

- **WHEN** a POSIX credential file grants a group or other permission bit, is owned by another user, or is a symlink
- **THEN** the request is answered 503 and nothing is sent to codex-lb

#### Scenario: non-ChatGPT auth file is refused

- **WHEN** `auth.json` declares an `auth_mode` other than `chatgpt` or carries a non-empty `OPENAI_API_KEY`
- **THEN** the request is answered 503 and nothing is sent to codex-lb

#### Scenario: token file with header injection is refused

- **WHEN** the LB token file contains whitespace, a second line, or a non-ASCII byte
- **THEN** the request is answered 503 and nothing is sent to codex-lb

### Requirement: Callers must present the local ChatGPT access token

The bridge MUST admit a request only when it carries exactly one
`Authorization` field of the form `Bearer <token>` and the token equals the
current `auth.json` access token under a constant-time comparison. A missing,
duplicate, malformed, or mismatched credential MUST be answered with a generic
403. The bridge MUST NOT answer any local authentication outcome with 401. A
bearer minted from an agent identity or any credential store other than
`auth.json` MUST fail closed with 403; the bridge MUST NOT disable client
features to avoid this.

#### Scenario: matching bearer is admitted

- **WHEN** a request carries `Authorization: Bearer <auth.json access token>`
- **THEN** the request is forwarded with the LB token instead

#### Scenario: mismatched or duplicated bearer is denied

- **WHEN** a request carries a different bearer, two `Authorization` fields, or a non-Bearer scheme
- **THEN** the bridge answers 403 without contacting codex-lb
- **AND** the response is never 401

### Requirement: Request targets and origins are confined to the Codex namespace

The bridge MUST forward only origin-form targets whose raw path is exactly
`/backend-api/codex` or starts with `/backend-api/codex/`, including a trailing
slash and routes or methods it does not recognize. It MUST reject `CONNECT` and
`TRACE`, absolute-form and asterisk-form targets, fragments, dot segments,
backslashes, empty segments (`//`), and case-insensitive percent-encoded dot,
slash, backslash, or percent, so nested encodings cannot form a traversal. It
MUST forward the raw path and raw query bytes unchanged. Every request MUST
carry exactly one `Host` equal to `127.0.0.1:<listen_port>` or
`localhost:<listen_port>`, and MUST NOT carry an `Origin` field. The bridge MUST
NOT emit CORS headers.

#### Scenario: namespace paths are forwarded byte-for-byte

- **WHEN** a request targets `/backend-api/codex/responses?a=%20b`
- **THEN** codex-lb receives the same raw path and query

#### Scenario: traversal attempts are rejected

- **WHEN** a request path contains `..`, `%2e`, `%2F`, `%5c`, `%252e`, a backslash, or `//`
- **THEN** the bridge rejects it without contacting codex-lb

#### Scenario: foreign Host or any Origin is refused

- **WHEN** a request carries a Host other than the loopback listen authority, two Host fields, or an Origin field
- **THEN** the bridge rejects it without contacting codex-lb

### Requirement: Credentials and forwarding metadata are replaced at the bridge

Before forwarding, the bridge MUST remove every case variant and duplicate of
`Authorization`, `Proxy-Authorization`, `X-OpenAI-Actor-Authorization`,
`ChatGPT-Account-ID`, `Cookie`, `Forwarded`, `X-Forwarded-*`, `X-Real-IP`,
`True-Client-IP`, `CF-Connecting-IP`, hop-by-hop fields, and fields nominated
by `Connection`, and MUST then add exactly one
`Authorization: Bearer <LB token>`. It MUST preserve every other end-to-end
request field, including duplicates, `User-Agent`, `originator`, `version`,
`x-codex-*`, and `x-openai-*` fields. From responses and WebSocket handshake
responses it MUST remove credential, `Set-Cookie`, authentication-challenge,
hop-by-hop, and `Connection`-nominated fields, and preserve the rest. Cookie
stripping is a deliberate v1 limit: no cookie crosses the bridge in either
direction.

#### Scenario: actor token never reaches codex-lb

- **WHEN** a request carries `X-OpenAI-Actor-Authorization` or `ChatGPT-Account-ID` in any casing
- **THEN** the forwarded request contains neither field
- **AND** its only credential is the LB bearer token

#### Scenario: client identity headers survive

- **WHEN** a request carries `User-Agent`, `originator`, and duplicated `x-codex-*` fields
- **THEN** codex-lb receives the same fields and values in the same multiplicity

### Requirement: HTTP relay is opaque and bounded

The HTTP relay MUST NOT decompress request or response bodies, MUST preserve
`Content-Encoding` and body bytes, MUST NOT store or send cookies, MUST ignore
proxy environment variables, MUST NOT synthesize `User-Agent`, `Accept`,
`Accept-Encoding`, or `Content-Type` fields the caller omitted, and MUST NOT
follow redirects or retry non-idempotent requests. An upstream 3xx or 401 MUST
be answered with a generic 502 that omits the upstream body, `Location`, and
authentication challenges; other upstream statuses, including 403, 426, and
429, MUST pass through. A request body larger than 128 MiB MUST be rejected with
413, before dispatch when its length is declared. Response bodies are bounded to
the same 128 MiB. If a failure happens after response bytes were sent, the
bridge MUST close the downstream connection instead of completing the response.
When the active-request limit is reached, new requests MUST be answered 503
immediately instead of waiting.

#### Scenario: upstream redirect is masked

- **WHEN** codex-lb answers 302 with a `Location`
- **THEN** the client receives 502 without the `Location` or upstream body

#### Scenario: upstream 401 is masked

- **WHEN** codex-lb answers 401
- **THEN** the client receives 502, so it does not treat the bridge as a ChatGPT login failure

#### Scenario: compressed bodies pass unchanged

- **WHEN** a request or response carries a `Content-Encoding` body
- **THEN** the opposite side receives the same encoded bytes and `Content-Encoding`

#### Scenario: truncated upstream stream is not disguised

- **WHEN** the upstream stream fails after the bridge started the response
- **THEN** the downstream connection is closed without a completed response

#### Scenario: saturation fails fast

- **WHEN** a request arrives while the active-request limit is in use
- **THEN** it is answered 503 without waiting

### Requirement: WebSocket relay preserves the session and never follows redirects

For a WebSocket upgrade, the bridge MUST open the codex-lb WebSocket before
accepting the downstream upgrade, without proxies, compression extensions, a
synthesized `User-Agent`, or redirect following, and with bounded open and
close deadlines and a 128 MiB message limit. An upstream handshake answered with
3xx or 401, or failing without a status, MUST be answered with a generic 502;
other upstream statuses, including 426, MUST pass through with their sanitized
headers. The bridge MUST negotiate `Sec-WebSocket-*` fields itself and accept
only the subprotocol that codex-lb selected, and MUST copy the allowed upstream
handshake response fields into the downstream 101 response. It MUST relay text
and binary messages in order with their type, relay sendable close codes and
reasons, and replace unsendable or missing codes. When either direction ends,
fails, stays idle past the idle limit, or exceeds the lifetime limit, the bridge
MUST cancel and await the other direction, close both sides, and release the
active-request slot.

#### Scenario: upgrade waits for codex-lb

- **WHEN** codex-lb refuses the WebSocket handshake with 426
- **THEN** the client receives 426 and no downstream upgrade occurs

#### Scenario: WebSocket redirect is not followed

- **WHEN** codex-lb answers the handshake with 307 and a `Location`
- **THEN** the bridge opens no second connection and answers 502

#### Scenario: close code is relayed

- **WHEN** codex-lb closes the session with code 4000 and a reason
- **THEN** the client receives close code 4000 with the same reason

#### Scenario: one side failing closes both

- **WHEN** the downstream client disconnects abruptly
- **THEN** the upstream WebSocket is closed and the active slot is released

### Requirement: Process shutdown closes sessions and bounds HTTP draining

When the bridge receives a graceful process-stop signal, it MUST stop admitting
new requests and close established WebSockets with code 1001. WebSocket closes
MUST run concurrently with a 10-second close deadline; a peer that does not
finish the close handshake MUST have its transport closed. A WebSocket handshake
that finishes after shutdown begins MUST NOT escape this cleanup. HTTP handlers
MUST receive a 5-second graceful completion window after WebSocket shutdown,
followed by a bounded 5-second cancellation window. Incomplete HTTP streams MUST
be disconnected without a fabricated completed response. Shutdown MUST NOT
retry or replay a request, and all relay pumps MUST be cancelled and awaited
before the process exits.

#### Scenario: SIGTERM closes open sessions and releases the port

- **GIVEN** an established WebSocket and an unfinished HTTP event stream
- **WHEN** the bridge process receives SIGTERM
- **THEN** the WebSocket peer receives close code 1001
- **AND** the HTTP stream either completes during its grace window or disconnects without a completed response
- **AND** the upstream observes only one dispatch of each request and the bridge exits with its listening port released

#### Scenario: an unresponsive WebSocket peer cannot hold shutdown open

- **WHEN** a WebSocket peer does not acknowledge the shutdown close frame
- **THEN** the bridge closes its transport no later than the 10-second close deadline and continues shutdown

### Requirement: The bridge keeps request metadata out of logs

The bridge MUST NOT emit access logs, MUST NOT let its WebSocket client library
log handshake headers or URIs at any log level, and MUST NOT surface tracebacks
or error messages that contain credentials, credential-bearing header values,
or credential file paths.

#### Scenario: debug logging does not expose the LB token

- **WHEN** the root logger is configured at DEBUG and a WebSocket session is relayed
- **THEN** no log record contains the LB token, the ChatGPT access token, or a request header value
