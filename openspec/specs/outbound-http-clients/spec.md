# outbound-http-clients Specification

## Purpose

Define outbound HTTP client behavior so upstream OAuth and API calls use stable headers, personas, and proxy handling.
## Requirements
### Requirement: OAuth authorize requests use a configurable originator persona
Browser OAuth authorize requests MUST include an `originator` query parameter. The service MUST default that parameter to `codex_chatgpt_desktop` and MUST let operators override it through configuration when they need a different first-party Codex persona.

#### Scenario: default OAuth authorize originator uses the Desktop persona
- **WHEN** the operator does not configure an override
- **THEN** the browser OAuth authorize URL includes `originator=codex_chatgpt_desktop`

#### Scenario: configured OAuth authorize originator falls back to the CLI persona
- **WHEN** the operator configures the OAuth authorize originator as `codex_cli_rs`
- **THEN** the browser OAuth authorize URL includes `originator=codex_cli_rs`

### Requirement: Upstream websocket handshakes auto-detect standard proxy environment variables

When operators don't explicitly configure `upstream_websocket_trust_env`, upstream websocket handshakes MUST honor standard outbound proxy environment variables before connecting directly.
Explicit configuration MUST still override auto-detection.

#### Scenario: secure websocket handshakes honor scheme-compatible env proxies by default

- **WHEN** an upstream websocket URL uses the `wss://` scheme
- **AND** `wss_proxy`, `socks_proxy`, `https_proxy`, or `all_proxy` is set
- **AND** `upstream_websocket_trust_env` is not explicitly configured
- **THEN** upstream websocket handshakes use the configured proxy instead of bypassing it

#### Scenario: plain websocket handshakes honor scheme-compatible env proxies by default

- **WHEN** an upstream websocket URL uses the `ws://` scheme
- **AND** `ws_proxy`, `socks_proxy`, `https_proxy`, `http_proxy`, or `all_proxy` is set
- **AND** `upstream_websocket_trust_env` is not explicitly configured
- **THEN** upstream websocket handshakes use the configured proxy instead of bypassing it

#### Scenario: ws handshakes preserve HTTPS proxy fallback

- **WHEN** an upstream websocket URL uses the `ws://` scheme
- **AND** `https_proxy` is set without a `ws_proxy` or `http_proxy` override
- **THEN** the upstream websocket handshake uses the `https_proxy` value before falling back to `all_proxy`

#### Scenario: explicit direct-connect override bypasses env proxies

- **WHEN** `upstream_websocket_trust_env=false`
- **AND** standard outbound proxy environment variables are set
- **THEN** upstream websocket handshakes connect directly without using those proxies

### Requirement: Runtime version status checks latest GitHub release

The service SHALL expose a dashboard-auth protected runtime version status API that reports the running codex-lb version, the latest known GitHub release version when available, whether an update is available, and the time of the latest lookup attempt. The lookup MUST be cached in-process to avoid per-request GitHub traffic, and lookup failures MUST NOT cause the API to fail.

#### Scenario: Latest release is newer than current version

- **WHEN** the running version is `1.19.0`
- **AND** the GitHub latest release tag is `v1.20.0`
- **THEN** the runtime version status reports `currentVersion: "1.19.0"`, `latestVersion: "1.20.0"`, and `updateAvailable: true`

#### Scenario: GitHub lookup fails

- **WHEN** the GitHub latest release lookup fails
- **THEN** the runtime version status API still returns the current version
- **AND** `updateAvailable` is `false`

### Requirement: Model refresh recovers from shared HTTP client transport failures

When the model registry refresh path fails before receiving an upstream HTTP response because of a transport-level error, the system MUST treat that failure as recoverable transport state, rebuild the shared outbound HTTP client, and retry the failed model-refresh operation at most once for the current failover cycle. HTTP status failures, invalid upstream payloads, and permanent authentication failures MUST NOT trigger shared-client rotation.

#### Scenario: model fetch transport failure rotates the shared client once

- **WHEN** a model refresh attempts to fetch upstream models for an active account
- **AND** the fetch fails with a timeout, `aiohttp.ClientError`, or OS-level transport error before an upstream HTTP response is received
- **THEN** the system rotates the shared outbound HTTP client
- **AND** retries the model fetch once with the replacement client
- **AND** does not perform additional client rotations for later transport errors in the same failover cycle

#### Scenario: token refresh transport failure also rotates the shared client once

- **WHEN** model refresh needs to refresh an account token before fetching models
- **AND** the token refresh fails with a timeout, `aiohttp.ClientError`, or OS-level transport error before an upstream HTTP response is received
- **THEN** the system rotates the shared outbound HTTP client
- **AND** retries the token refresh once with the replacement client
- **AND** preserves existing permanent/non-permanent refresh error classification for non-transport failures

### Requirement: Shared outbound HTTP client rotation preserves in-flight users

Callers that use the default shared outbound HTTP session or retry client MUST lease the current shared client for the full duration of their upstream operation. Rotating the shared client MUST make new callers use the replacement client while deferring closure of the retired client until all active leases on that retired client have released. Process shutdown MAY force-close active and retired clients to keep shutdown bounded.

#### Scenario: in-flight request keeps using retired client until release

- **WHEN** an upstream operation acquires a lease on the current shared client
- **AND** model refresh rotates the shared client after a transport failure
- **THEN** new shared-client callers use the replacement client
- **AND** the retired client remains open until the in-flight operation releases its lease

#### Scenario: long-lived operations hold one lease across their whole upstream exchange

- **WHEN** a shared-client caller performs a streaming response, compact request, transcription request, usage fetch, token refresh, OAuth call, model fetch, or file create/finalize poll loop
- **THEN** the caller holds a shared-client lease until the operation has finished consuming the upstream response or poll loop
- **AND** a concurrent shared-client rotation does not close that operation's client mid-exchange

#### Scenario: shutdown force-closes active leases

- **WHEN** the application is shutting down
- **AND** active leases still exist on the current or retired shared client
- **THEN** global HTTP client close is allowed to force-close those clients instead of waiting indefinitely for long-lived streams

### Requirement: Process-wide network failures rotate shared transport state

The service MUST classify local DNS resolver and host-route failures separately from account-specific upstream failures. Classification MUST come from typed exception provenance or an already-preserved stable internal code, not from matching arbitrary upstream message text. When such a failure affects the current shared outbound HTTP client, the service MUST make subsequent callers use a replacement client while preserving active leases on the retired client. Concurrent failures from the same retired generation MUST NOT cause repeated client rotations. Replacement construction and cleanup MUST remain cancellation-safe: an interrupted or failed replacement MUST close partially created resources and leave the previous generation current.

#### Scenario: DNS failure rotates the current shared client once

- **WHEN** concurrent outbound operations using the same shared client fail with a local DNS resolution error
- **THEN** the shared client is replaced once
- **AND** subsequent operations lease the replacement client
- **AND** active users of the retired client retain their lease until release

#### Scenario: Failure from a retired client does not rotate its replacement

- **WHEN** one caller has already replaced the shared client after a process-wide network failure
- **AND** another caller from the retired client reports the same failure
- **THEN** the replacement client remains current
- **AND** no additional replacement is created for that retired generation

#### Scenario: Upstream message text does not manufacture local provenance

- **WHEN** a genuine upstream failure uses `upstream_unavailable` and a message such as `Network is unreachable`
- **AND** no typed local-network classification accompanies it
- **THEN** the failure does not enter process-network recovery

#### Scenario: Cancelled replacement preserves the live generation

- **WHEN** shared-client replacement is cancelled after creating only part of the replacement transport
- **THEN** all partially created sessions and connectors are closed
- **AND** the previously current client generation remains current

### Requirement: Process-wide network failures are account neutral

The proxy MUST NOT record a transient, permanent, quota, rate-limit, or circuit-breaker health failure against an account when an attempt fails because the local process cannot resolve or route to the upstream host. Routed proxy transport failures MUST retain a credential-safe machine-readable classification after the original exception message is sanitized. A permanent missing proxy hostname MUST remain an endpoint-scoped proxy failure rather than entering process-wide recovery.

#### Scenario: Wi-Fi transition does not poison account health

- **WHEN** an upstream attempt fails with a classified local DNS or host-route failure
- **THEN** the selected account's health counters and cooldown state are unchanged
- **AND** the selected account's circuit breaker is unchanged
- **AND** continuity ownership remains pinned to that account

#### Scenario: Routed transient DNS failure remains account neutral after sanitization

- **WHEN** an HTTP or WebSocket attempt through a resolved upstream proxy route fails with transient DNS or local route loss
- **THEN** the credential-safe routed error carries the process-network classification
- **AND** the selected account's health and circuit-breaker state are unchanged

#### Scenario: Missing proxy hostname remains endpoint scoped

- **WHEN** resolving a configured upstream proxy hostname fails with a permanent name-not-found result
- **THEN** the failure remains `upstream_unavailable`
- **AND** the proxy does not classify the host process as disconnected

### Requirement: Outbound HTTP and WebSocket sessions transparently tunnel through a SOCKS proxy

The outbound HTTP and WebSocket clients MUST use a configured SOCKS proxy for all
upstream connections when any supported proxy environment variable carries a
SOCKS URL.
Configuring a SOCKS proxy MUST NOT require code changes — setting an environment
variable MUST be sufficient.

#### Scenario: SOCKS5 proxy is active — HTTP session uses ProxyConnector

- **GIVEN** `SOCKS_PROXY=socks5://gateway:1080` (or any equivalent env var below)
- **WHEN** the shared outbound HTTP client is initialised
- **THEN** the HTTP session uses a `ProxyConnector` built from that URL
- **AND** `trust_env=False` is passed to `aiohttp.ClientSession` to prevent double-proxying

#### Scenario: SOCKS5 proxy is active — WebSocket session routes through proxy when opt-in

- **GIVEN** a SOCKS URL is detected in the environment
- **AND** `upstream_websocket_trust_env=True` is configured
- **WHEN** the shared outbound WebSocket client is initialised
- **THEN** the WebSocket session uses a `ProxyConnector` built from the same SOCKS URL
- **AND** `trust_env=False` is passed to that session

#### Scenario: SOCKS5 proxy is active — WebSocket session connects directly when not opted in

- **GIVEN** a SOCKS URL is detected in the environment
- **AND** `upstream_websocket_trust_env` is not set to `True`
- **WHEN** the shared outbound WebSocket client is initialised
- **THEN** the WebSocket session uses a plain `TCPConnector` (unchanged behaviour)

#### Scenario: No SOCKS proxy configured — behaviour is identical to before

- **GIVEN** no SOCKS URL is present in any proxy environment variable
- **WHEN** the shared outbound HTTP client is initialised
- **THEN** both sessions use `aiohttp.TCPConnector` as before
- **AND** `trust_env` is passed unchanged per existing settings

### Requirement: SOCKS proxy URL detection follows a defined env var precedence

The service MUST probe the following environment variables in order and return the
first value that carries a SOCKS scheme:

1. `SOCKS_PROXY`
2. `socks_proxy`
3. `ALL_PROXY`
4. `HTTPS_PROXY`
5. `HTTP_PROXY`
6. `all_proxy`
7. `https_proxy`
8. `http_proxy`

Accepted input schemes: `socks5://`, `socks5h://`, `socks4://`, `socks4a://`.

Additional normalisation rules:
- Values MUST be stripped of leading/trailing whitespace before inspection.
- A bare `http://` scheme in `SOCKS_PROXY` or `socks_proxy` MUST be normalised
  to `socks5://` (accommodates misconfigured env vars while keeping the URL
  parseable by the configured proxy connector).
- `socks5h://` and `socks4a://` values MUST be normalised to `socks5://` and
  `socks4://` before connector construction because the configured proxy parser
  rejects the extended schemes.
- `HTTP_PROXY` and `http_proxy` MUST be skipped when `REQUEST_METHOD` is set in
  the environment (httpoxy / CGI security convention).

#### Scenario: Whitespace-padded value is accepted and returned stripped

- **GIVEN** `SOCKS_PROXY="  socks5://gateway:1080  "`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080` (no surrounding whitespace)

#### Scenario: Bare `http://` scheme in `SOCKS_PROXY` is normalised

- **GIVEN** `socks_proxy=http://gateway:1080`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080`

#### Scenario: Extended SOCKS schemes are normalised before connector use

- **GIVEN** `SOCKS_PROXY=socks5h://gateway:1080`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080`

#### Scenario: CGI environment skips `HTTP_PROXY`

- **GIVEN** `REQUEST_METHOD=GET` is set
- **AND** `HTTP_PROXY=socks5://gateway:1080` is the only SOCKS var
- **WHEN** the SOCKS URL is resolved
- **THEN** the result is `None` (variable is ignored)

### Requirement: Non-native upstream requests use the Codex CLI client fingerprint

The service MUST normalize the outbound client fingerprint to the first-party
Codex CLI (`codex_cli_rs`) persona when forwarding a proxied request to the
upstream Codex backend that did not originate from a native Codex client. This
normalization MUST apply on every upstream egress path: the http builder, the
internal auto-transport websocket builder, and the client-facing
`/v1/responses` websocket egress builder
(`app/core/clients/proxy_websocket.py`). With `upstream_stream_transport="auto"`
a non-native client carrying a `x-codex-turn-state` continuity header is routed
onto the internal websocket path, and a direct websocket SDK caller reaches
upstream through the `/v1/responses` egress builder; normalizing only a subset
of these paths would let the un-normalized path reach upstream with its
downgraded fingerprint intact. The service MUST NOT modify the fingerprint of
native Codex client requests on any transport.

A request is considered **native** when its inbound `User-Agent` begins with a
known Codex client token (`codex_cli_rs`, `codex-tui`, `codex_exec`,
`codex_sdk_ts`, `codex_vscode`, `Codex Desktop`, or a value starting with
`Codex `) OR it carries an `originator` header whose value is in the native
Codex originator set (which MUST include every first-party originator the
backend whitelists, e.g. `codex_cli_rs`, `codex_vscode`, `codex_sdk_ts`).
Transport/continuity headers (`x-codex-turn-state` and other `x-codex-*`
stream headers) MUST NOT be treated as a native signal, because a non-native
client replays the upstream-issued `x-codex-turn-state` token for continuity;
treating it as native would let that follow-up reach upstream with its
downgraded fingerprint intact.

For a non-native request, the service MUST:

- Set the outbound `User-Agent` to
  `codex_cli_rs/<version> (<os>; <arch>) <terminal>`, where `<version>` is the
  cached Codex client version (falling back to the configured client-version
  default when no cached version is available) and `<os>`, `<arch>`,
  `<terminal>` are operator-configurable with defaults `Mac OS 26.5.0`,
  `arm64`, and `iTerm.app/3.6.10`.
- Remove SDK-only fingerprint headers `x-openai-client-version`,
  `x-openai-client-os`, `x-openai-client-arch`, `x-openai-client-id`, and
  `x-openai-client-user-agent`, as well as every `x-stainless-*` header (the
  OpenAI SDK fingerprint family the API layer uses to detect SDK callers).
- Remove inbound `originator` and `version` headers case-insensitively, then
  set `originator: codex_cli_rs` and `version: <version>`, where `<version>` is
  the same cached Codex client version used in the outbound `User-Agent`.
- Emit the upstream account header as PascalCase `ChatGPT-Account-Id`.
- Preserve continuity headers (`x-codex-turn-state` and other `x-codex-*`
  stream headers) on the outbound request so sticky routing is unaffected.

Resolving the fingerprint version for an outbound request MUST NOT perform a
blocking network call on the request path; the version is read from an
in-process cache. Every replica MUST warm that cache when its model refresh
loop starts and MUST refresh it on every loop tick regardless of scheduler
leadership (the lookup is a public release lookup that carries no account
credential), so a non-leader replica never presents the configured
client-version fallback beyond its first tick. A warm-up failure MUST NOT stop
the model refresh tick. The configured fallback (`model_registry_client_version`)
SHALL track the current public Codex CLI release.

#### Scenario: non-native SDK http request is rewritten to the Codex CLI fingerprint

- **WHEN** an http upstream request arrives with `User-Agent: OpenAI/Python 2.24.0`,
  untrusted `originator` / mixed-case `Version` values, and
  `x-openai-client-version` / `x-openai-client-os` / `x-stainless-os` headers
- **THEN** the outbound `User-Agent` is `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** the `x-openai-client-version`, `x-openai-client-os`,
  `x-openai-client-arch`, `x-openai-client-id`, and `x-openai-client-user-agent`
  headers are absent from the outbound request
- **AND** every `x-stainless-*` header is absent from the outbound request
- **AND** the only outbound identity values are `originator: codex_cli_rs` and
  `version: <version>` matching the version embedded in `User-Agent`

#### Scenario: native Codex http request is left unchanged

- **WHEN** an http upstream request arrives with `User-Agent: codex_exec/0.142.1 (Mac OS 27.0.0; arm64) unknown (codex_exec; 0.142.1)`
- **THEN** the outbound `User-Agent` equals the inbound `User-Agent`
- **AND** the request fingerprint is not normalized

#### Scenario: first-party Codex SDK request is left unchanged

- **WHEN** an http upstream request carries an `originator: codex_sdk_ts` header
  (a first-party originator the backend whitelists)
- **THEN** the outbound request is treated as native
- **AND** its `User-Agent` and `originator` header are not rewritten or stripped

#### Scenario: non-native request replaying a continuity token is still normalized

- **WHEN** an http upstream request arrives with `User-Agent: OpenAI/Python 2.24.0`
  and an `x-codex-turn-state` continuity header
- **THEN** the request is treated as non-native and its fingerprint is normalized
- **AND** the `x-codex-turn-state` header is preserved on the outbound request

#### Scenario: non-native websocket request carrying a continuity token is normalized

- **WHEN** the upstream stream transport resolves to websocket for a non-native
  request with `User-Agent: OpenAI/Python 2.24.0`, an `x-openai-client-version`
  header, and an `x-codex-turn-state` continuity header
- **THEN** the outbound websocket `User-Agent` is
  `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** SDK identity headers are absent and the outbound request carries
  `originator: codex_cli_rs` and `version: <version>`
- **AND** the upstream account id is carried under the PascalCase header name
  `ChatGPT-Account-Id`
- **AND** the `x-codex-turn-state` header is preserved on the outbound request

#### Scenario: native Codex websocket request is left unchanged

- **WHEN** the upstream stream transport resolves to websocket for a request
  with `User-Agent: codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10`
- **THEN** the outbound websocket `User-Agent` equals the inbound `User-Agent`
- **AND** the account id is carried under the lowercase header `chatgpt-account-id`

#### Scenario: non-native client-facing responses websocket request is normalized

- **WHEN** a non-native SDK connects directly to the `/v1/responses` websocket
  endpoint with `User-Agent: OpenAI/Python 2.24.0`, `x-openai-client-version`,
  and `x-stainless-*` headers
- **THEN** the upstream responses websocket `User-Agent` is
  `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** the `x-openai-client-*` and `x-stainless-*` headers are absent while
  `originator: codex_cli_rs` and `version: <version>` are present
- **AND** the upstream account id is carried under the PascalCase header name
  `ChatGPT-Account-Id`
- **AND** the required responses websocket beta header is still present

#### Scenario: account header uses Codex CLI casing on a normalized request

- **WHEN** a non-native http request is normalized and an upstream account id is present
- **THEN** the outbound request carries the account id under the PascalCase
  header name `ChatGPT-Account-Id`

#### Scenario: per-account upstream diagnostics survive normalization

- **WHEN** upstream request logging is enabled and a normalized non-native
  request carries its account id under the PascalCase `ChatGPT-Account-Id` header
- **THEN** the upstream request start/complete log entries record the account id
  rather than `None`, so per-account diagnostics are preserved regardless of the
  header casing produced by normalization

#### Scenario: fingerprint version falls back to the configured default

- **WHEN** the Codex version cache has no cached version
- **AND** a non-native http request is normalized
- **THEN** the outbound `User-Agent` uses the configured client-version default
  for `<version>`
- **AND** the outbound `version` header uses that same configured default
- **AND** resolving the version does not perform a network call on the request path

#### Scenario: Non-leader replica warms the client version itself

- **GIVEN** a replica that does not hold the scheduler leader lease (for example the live color of a blue/green pair whose standby still holds the lease)
- **WHEN** its model refresh loop ticks
- **THEN** it fetches and caches the current Codex client version before the leader-gated model refresh
- **AND** non-native requests it forwards carry that version in `User-Agent` and `version` instead of the configured fallback

#### Scenario: Version warm-up failure does not stop the refresh tick

- **GIVEN** the public release lookup fails on a replica
- **WHEN** its model refresh loop ticks
- **THEN** the failure is logged, the cached or fallback version is kept, and the leader-gated refresh (or non-leader reconcile) still runs

### Requirement: OAuth token exchange must use a proxy pool when active proxy bindings exist

When any active `AccountProxyBinding` records exist in the database, OAuth token exchange (authorization code exchange, device code request, and device token poll) MUST resolve a route from the configured default pool before opening a network connection. If no default pool can be resolved, the OAuth operation MUST fail closed with a descriptive error instead of silently falling back to direct egress. When no active proxy bindings exist, direct egress or environment proxy MAY be used as before.

#### Scenario: OAuth fails closed when bindings exist but no default pool is configured
- **GIVEN** one or more active `AccountProxyBinding` records exist
- **AND** no default pool is configured
- **WHEN** the OAuth token exchange is attempted
- **THEN** the operation MUST fail before opening any network connection
- **AND** the error MUST indicate that no upstream proxy route is available

#### Scenario: OAuth uses default pool when bindings exist and pool is configured
- **GIVEN** one or more active `AccountProxyBinding` records exist
- **AND** a default pool is configured with an active endpoint
- **WHEN** the OAuth token exchange is attempted
- **THEN** the request MUST go through the default pool's endpoint

#### Scenario: OAuth preserves direct egress when no proxy bindings exist
- **GIVEN** no active `AccountProxyBinding` records exist in the database
- **WHEN** the OAuth token exchange is attempted
- **THEN** the request MAY use direct egress or environment proxy as before

### Requirement: Token refresh must fail closed when account binding exists but route is unavailable

When an account has an active proxy binding but route resolution returns `None` (e.g., binding toggled inactive, pool deleted), the token refresh MUST raise an error instead of silently falling back to direct egress. This prevents an IP split after the account has been associated with a proxy.

#### Scenario: Refresh fails closed when binding becomes unavailable
- **GIVEN** an account has an active proxy binding at refresh start time
- **AND** the binding's pool has no active endpoint at resolution time
- **WHEN** a token refresh is attempted
- **THEN** the refresh MUST raise an upstream proxy unavailable error
- **AND** it MUST NOT silently use direct egress

### Requirement: Upstream SSE framing scans each byte a bounded number of times

The upstream SSE event reader MUST NOT rescan previously scanned buffer bytes on each network read; framing cost MUST be linear in event size so a single large event (up to the fixed 16 MiB event-size cap) cannot stall the shared event loop. Framing semantics MUST be unchanged: all separator forms (`\r\n\r\n`, `\n\n`, `\r\r`) are honored, including separators straddling read boundaries, and event-size limits and idle timeouts apply as before.

#### Scenario: Large event frames in linear time

- **GIVEN** a single SSE event several megabytes long arriving across many reads
- **WHEN** the reader frames the stream
- **THEN** each received byte is scanned at most a bounded number of times (no full-buffer rescans per read)
- **AND** the event is delivered intact

#### Scenario: Separator straddling a read boundary still terminates the event

- **GIVEN** an event whose `\r\n\r\n` separator is split across two reads
- **WHEN** the reader frames the stream
- **THEN** the event terminates exactly at the separator and the following event is framed normally

### Requirement: Upstream connectors persist across interactive turn gaps

The shared upstream TCP connectors MUST configure connection keepalive of at least 90 seconds and a DNS cache TTL of at least 300 seconds, so consecutive interactive requests reuse pooled connections and resolved names instead of re-handshaking per turn.

Because pooled connections outlive the requests that opened them, the connectors MUST
also enable OS-level TCP keepalive probes on upstream sockets, so a connection dropped
by an intermediary is reported as a transport error rather than waiting for an
application-level timeout. Probe tuning beyond enabling keepalive is best-effort:
platforms that do not expose the per-socket knobs MUST still enable keepalive and MUST
NOT fail client construction.

#### Scenario: Connector construction pins reuse settings

- **WHEN** the shared HTTP client initializes its direct TCP connectors
- **THEN** they are constructed with `keepalive_timeout >= 90` and `ttl_dns_cache >= 300`

#### Scenario: Pooled sockets carry keepalive probes

- **WHEN** the shared HTTP client creates an upstream socket
- **THEN** `SO_KEEPALIVE` is enabled on that socket
- **AND** client construction succeeds even when per-socket probe tuning is unavailable

### Requirement: Packaged native egress is preferred only across a replay-safe boundary

When the fixed packaged `codex-lb-native-egress` executable is available, direct and account-routed Codex model-discovery, JSON/raw/multipart HTTP, Responses HTTP/SSE, and Responses or Live WebSocket calls MUST prefer it over the corresponding Python data-plane client. Python MUST retain ownership of account selection, route resolution, ordered proxy endpoint fallback, route metadata, and health classification, while each native command MUST target exactly one concrete direct or proxy endpoint. The worker MUST reuse one persistent helper generation and compatible reqwest HTTP/2 client pools across HTTP requests, and MUST multiplex concurrent HTTP and WebSocket operations without cross-delivering events. Native calls MUST preserve standard direct HTTP/HTTPS/SOCKS proxy environment resolution and `NO_PROXY` bypass behavior, and routed calls MUST use the resolved endpoint without consulting environment proxy variables. Python fallback is permitted only when the executable is absent or cannot be spawned. Once a helper process launches, a malformed, timed-out, or incompatible hello/negotiation exchange MUST fail closed without dispatching the operation to Python. A non-idempotent request, WebSocket handshake, or WebSocket frame MUST NOT fall back to Python after its native command may have been dispatched. Helper failure MUST fail operations from that generation without replay and MAY be recovered only by starting a new generation for a later operation. A confirmed pre-dispatch routed connection failure MAY use the next endpoint under the existing route policy, while a TLS verification failure or ambiguous delivery MUST NOT gain new replay eligibility.

Credential-bearing routed proxy endpoints MAY use `http://`, `socks5://`, or
`socks5h://` transport; the credential then crosses the LB-to-proxy hop
unencrypted. Route resolution MUST accept such endpoints, MUST mark the
resolved endpoint as carrying plaintext credentials, and MUST log one
credential-free warning per endpoint per process. Credentials MUST still reach
aiohttp through the CONNECT `Proxy-Authorization` header (TLS targets) or the
SOCKS connector's username/password parameters, never through a logged URL,
and a credentialed proxy route MUST still require an `https`/`wss` upstream
target.

#### Scenario: Plaintext proxy credentials fail before connector selection

- **GIVEN** a routed endpoint contains credentials
- **AND** the upstream target is not an `https`/`wss` URL, so aiohttp could not carry the credential on a CONNECT tunnel
- **WHEN** codex-lb resolves the route for that operation
- **THEN** the operation fails closed before either native or Python egress is selected
- **AND** neither connector receives the credential-bearing route

#### Scenario: Plaintext proxy credentials are accepted and flagged

- **GIVEN** a routed endpoint contains credentials and uses `http://`, `socks5://`, or `socks5h://`
- **WHEN** codex-lb resolves the route for an HTTP or WebSocket operation
- **THEN** route resolution succeeds with the endpoint marked as carrying plaintext credentials
- **AND** one warning naming the endpoint id, scheme, host, and port (never the credential) is logged the first time that endpoint resolves in the process

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

### Requirement: Client-to-LB routing hints remain hop-local

The service MUST discard inbound `x-codex-routing-hint` values case-insensitively.
Proxy-routed subscription Responses requests with a known model MUST synthesize a
new hint from the final model and service tier when opening an HTTP request or
WebSocket handshake. Inbound values MUST
NOT determine that hint. Inbound LB API-key authentication MUST NOT prevent
synthesis for a selected subscription account. Non-subscription transports MUST
NOT synthesize a Codex-backend hint.

#### Scenario: Inbound HTTP hint is replaced
- **GIVEN** an inbound request advertises a different model or tier in its hint
- **WHEN** a subscription-account Responses HTTP request is built
- **THEN** any synthesized hint MUST reflect the final outbound body

#### Scenario: Inbound WebSocket hint is discarded
- **GIVEN** an inbound handshake includes any case spelling of the hint header
- **WHEN** upstream WebSocket handshake headers are built
- **THEN** the inbound value MUST NOT be forwarded
- **AND** any synthesized hint MUST use trusted request state

### Requirement: Native HTTP/2 startup profile matches measured Codex

Every persistent native HTTP client pool entry MUST use the measured Codex
initial HTTP/2 stream receive window, connection receive window, maximum frame
size, and maximum header-list size. It MUST NOT enable adaptive startup flow
control when that would replace the explicit profile. The maintained profile is
2,097,152 bytes for the stream receive window, 5,242,880 bytes for the
connection receive window, 16,384 bytes for maximum frame size, and 16,384
bytes for maximum header-list size.

#### Scenario: Native helper starts a new HTTP/2 connection

- **WHEN** a direct or routed native HTTP request creates a fresh connection
- **THEN** its ordered initial SETTINGS and pre-request connection-control shape
  match the maintained direct-Codex profile
- **AND** the route choice does not select a different HTTP/2 profile

### Requirement: Native Codex header replacement preserves wire order

For an inbound native Codex request, codex-lb MUST replace authorization,
accept, content-type, and selected-account values at the position and spelling
of their existing case-insensitive field names. It MUST append a field only
when that field is absent and MUST NOT emit duplicate case variants.

#### Scenario: Native Responses request already contains singleton fields

- **GIVEN** a native Codex request contains ordered authorization, accept,
  content-type, and account-id fields
- **WHEN** codex-lb installs the selected account and upstream values
- **THEN** those field names retain their relative wire order and spelling
- **AND** each case-insensitive singleton occurs exactly once

### Requirement: Model discovery uses the direct Codex header sequence

Subscription model-discovery requests MUST emit authorization, optional account
id, accept, originator, and User-Agent in the maintained direct-Codex order.
The client version MUST remain in the model-discovery query and User-Agent and
MUST NOT be duplicated into a standalone `version` header unless a newer
direct-client profile explicitly requires it.

#### Scenario: Authenticated model discovery is serialized

- **WHEN** codex-lb fetches models for an authenticated ChatGPT account
- **THEN** the decoded header-name order matches the maintained direct profile
- **AND** no standalone `version` header is present

### Requirement: Native helper compatibility is negotiated before dispatch

Each newly started native helper generation MUST complete a bounded,
versioned client/server hello exchange before accepting an HTTP or WebSocket
command. The Python adapter MUST require the negotiated protocol version and
every capability used by its current call sites. A helper that is present but
malformed, times out during negotiation, selects an unsupported version, or
omits a required capability MUST fail as an incompatible protocol before
dispatch and MUST NOT be treated as an unavailable helper eligible for Python
fallback.

#### Scenario: Compatible helper generation starts

- **WHEN** the helper selects a mutually supported protocol version and reports every required capability
- **THEN** the adapter starts the generation reader and may dispatch requests
- **AND** later compatible requests reuse that negotiated generation

#### Scenario: Installed helper is incompatible

- **WHEN** the helper handshake times out, is malformed, selects an unsupported version, or lacks a required capability
- **THEN** the adapter terminates that process before dispatch
- **AND** the attempted operation fails without Python replay

### Requirement: Native SSE framing is an explicitly negotiated transport mode

The native protocol MUST advertise and the Python adapter MUST require
`http_sse_v1` before dispatch. HTTP requests MAY include SSE framing options
with positive idle timeout and event byte limit. Without these options, and
for HTTP statuses at least 400, the worker MUST preserve raw chunk output.
With these options and a successful HTTP response selected for framing, Rust
MUST emit complete SSE text blocks and own byte framing and the deadline between body reads.
Python MUST NOT reframe these blocks or replay a dispatched request.
IPC text fragments MUST be bounded to at most 16 KiB of UTF-8, with an explicit
continuation flag; the adapter MUST join them before exposing an event and
MUST reject a clean EOF that leaves an incomplete event.

#### Scenario: Old helper cannot silently ignore framing options

- **WHEN** an installed helper omits `http_sse_v1`
- **THEN** negotiation fails before HTTP dispatch
- **AND** the adapter does not fall back to another transport

#### Scenario: Partial bytes keep the upstream stream active

- **WHEN** body bytes arrive within each configured idle interval without completing an SSE block
- **THEN** the Rust body-read deadline resets on activity
- **AND** no premature event-wait timeout is introduced in Python

#### Scenario: HTTP error and ordinary body consumption

- **WHEN** SSE options are absent or the HTTP response status is at least 400
- **THEN** consumers receive the ordinary raw body chunk contract

#### Scenario: One framed request fails or is cancelled

- **WHEN** one framed stream exceeds its byte limit, times out, or is cancelled
- **THEN** only that attempt terminates and its stream registration is released
- **AND** unrelated requests sharing the helper remain usable
- **AND** failure diagnostics do not contain upstream body content or credentials

### Requirement: Routed native SSE options preserve transport ownership

Unbuffered routed HTTP requests MAY supply typed native SSE options.
`CodexClient` MUST pass these options unchanged to each native endpoint attempt
and MUST reject their use with buffered response consumption before dispatch.
It MUST NOT forward native-only options to a Python HTTP client. Each native
attempt MUST use only its resolved proxy endpoint, and successful results MUST
retain selected route metadata and native framed-response consumption.

#### Scenario: Pre-dispatch endpoint fallback preserves framing limits

- **WHEN** an unbuffered routed native POST has a confirmed replay-safe connect failure at its first endpoint
- **THEN** the next eligible endpoint receives the same framing limits
- **AND** the result records that endpoint and fallback use

#### Scenario: Missing helper uses the same resolved Python route

- **WHEN** the helper is unavailable before dispatch for a routed SSE request
- **THEN** Python transport uses the resolved endpoint and ordinary SSE parser
- **AND** native-only SSE options do not reach the HTTP client

#### Scenario: Buffered operation cannot select framed consumption

- **WHEN** a caller supplies native SSE options with buffered response consumption
- **THEN** the request fails before either native or Python dispatch

### Requirement: Compact native framing supports response content negotiation

The native protocol MUST advertise and the adapter MUST require
`http_compact_sse_v1` before dispatching content-type-aware SSE requests.
Such requests MUST frame successful responses when the Content-Type media type
is exactly `text/event-stream`, ignoring parameters and case, or when the header
is absent or empty. Other successful
responses and HTTP errors MUST retain raw body consumption. Ordinary SSE
requests without the content-type-aware option MUST retain their existing
framing behavior. A null native total timeout MUST mean no total deadline;
positive explicit total, connection, and SSE idle limits MUST be preserved.
The compact SSE idle limit MUST retain the existing Python policy: use the
effective compact timeout when set, otherwise use `stream_idle_timeout_seconds`.

#### Scenario: Compact success returns JSON

- **WHEN** a compact request receives a successful application/json response
- **THEN** the worker and adapter expose raw bytes for the existing JSON parser
- **AND** SSE event limits and decoding are not applied to that JSON body

#### Scenario: Compact success returns SSE or omits Content-Type

- **WHEN** a compact success has a text/event-stream or absent/empty Content-Type
- **THEN** Rust owns byte framing, original-byte limits, and body-read idle deadlines
- **AND** Python consumes framed text without rescanning bytes

#### Scenario: Non-SSE media type mentions event-stream

- **WHEN** compact succeeds with `text/event-stream+json` or a JSON Content-Type parameter containing `text/event-stream`
- **THEN** native and missing-helper Python transports use raw-body JSON parsing
- **AND** the SSE event byte limit does not apply to the JSON body

#### Scenario: Explicit compact timeout preserves its idle budget

- **WHEN** a compact request has an effective compact timeout longer than the ordinary stream idle timeout
- **THEN** native and missing-helper Python transports permit a body-read gap within that compact timeout
- **AND** the compact total deadline still bounds the complete request

#### Scenario: Optional total timeout

- **WHEN** compact has no total timeout
- **THEN** native transport does not substitute a default total timeout
- **AND** its configured connection and SSE idle limits remain active

#### Scenario: Incompatible helper

- **WHEN** an installed helper lacks http_compact_sse_v1
- **THEN** the adapter fails before dispatch rather than silently ignoring compact options
- **AND** it does not replay through Python

### Requirement: Native compact collection is negotiated and bounded across IPC

The adapter MUST require `http_compact_collect_v1` before requesting native
compact collection. Collection MUST apply only to successful responses selected
for SSE by the compact Content-Type contract. Other bodies MUST retain raw-body
handling. Collected result IPC text fragments MUST NOT exceed 16 KiB of UTF-8.
The adapter MUST reject malformed or truncated results without replaying the
request. Missing-helper fallback MUST remain limited to the pre-dispatch boundary.

#### Scenario: Large collected result

- **WHEN** a compact result exceeds one IPC text fragment
- **THEN** the adapter reconstructs one complete result with all unknown fields intact
- **AND** ready consumers receive scheduling opportunities between fragments

#### Scenario: Incompatible installed helper

- **WHEN** a launched helper lacks the compact collection capability
- **THEN** negotiation fails before dispatch and no Python replay occurs

#### Scenario: Cancellation while collecting

- **WHEN** a compact caller cancels before a terminal result
- **THEN** its native request and owned response/session close
- **AND** another request sharing the helper remains usable

### Requirement: Native event dispatch schedules ready consumers

The native helper event reader MUST give ready response consumers a scheduling
opportunity between accepted events, including when helper output is already
buffered. A burst exceeding the per-request queue capacity MUST complete without
data loss when its consumer keeps draining. A stalled consumer MUST retain a
bounded queue and fail independently without blocking sibling requests.

#### Scenario: Buffered burst with active consumer

- **GIVEN** a helper emits more events than the queue capacity in one buffered burst
- **WHEN** the caller continuously consumes the response body
- **THEN** all body bytes arrive in order and the response completes

#### Scenario: Stalled consumer shares the helper

- **GIVEN** one caller stops consuming while another request shares its helper
- **WHEN** the stalled request exceeds its bounded event queue
- **THEN** only the stalled request fails and the other request completes

#### Scenario: Responses consumes buffered native bursts

- **GIVEN** direct or account-routed Responses uses the native helper
- **WHEN** framed SSE events, raw JSON success chunks, or raw HTTP error chunks arrive in a buffered burst exceeding queue capacity
- **THEN** an active consumer receives ordered SSE events, the complete JSON response, or the original HTTP error respectively
- **AND** the response is not replaced by a consumer-backpressure error

### Requirement: Interpreted Responses SSE is negotiated across IPC

The adapter MUST require `http_responses_events_v1` before requesting interpreted
Responses events. Ordinary framing and compact collection MUST retain their
separate contracts. Interpreted text fragments MUST remain at most 16 KiB of
UTF-8. Type metadata MUST remain at most 16 KiB of UTF-8 and count toward the
queue byte budget; longer types MUST use Python normalization without metadata.
Only the final fragment MAY carry a type or request Python normalization.
Event type and Python-normalization metadata MUST be validated before use;
malformed or truncated events MUST fail without replay. Body-read deadlines,
original-byte event limits, cancellation and ready-consumer scheduling MUST remain
active. Missing-helper fallback MUST occur only before dispatch.

#### Scenario: Fragmented interpreted event

- **WHEN** one interpreted event spans multiple IPC fragments
- **THEN** the adapter emits one complete event with its validated metadata
- **AND** an active consumer can drain a burst beyond queue capacity

#### Scenario: Incompatible installed helper

- **WHEN** the installed helper lacks the required interpretation capability
- **THEN** the adapter rejects negotiation before dispatch without Python replay

### Requirement: Native SSE writes coalesce ready records without delaying delivery

The native helper MAY coalesce SSE IPC records already available from the current
body read. It MUST bound each coalesced write by encoded bytes and record count,
allowing an individual existing protocol record larger than the batch byte budget
to be written alone. It MUST flush all ready records before another upstream read
and before terminal or framing-error delivery. It MUST NOT wait for another event
or a batching timer. Existing JSON-line records, ordering, text-fragment bounds,
consumer scheduling and replay rules MUST remain unchanged.

Cancellation during an output write MUST NOT truncate or interleave IPC records.
Any accepted but unfinished write MUST remain owned by the shared output until it
completes or the output itself fails. A subsequent writer MUST finish that output
before emitting its own record.

#### Scenario: Quiet upstream after one event

- **WHEN** one event arrives and the upstream waits for the next request action
- **THEN** the event reaches the consumer without requiring another body read or EOF

#### Scenario: Valid prefix before a framing failure

- **WHEN** ready valid events precede an oversized event in the same body read
- **THEN** the valid prefix arrives in order before the typed size failure

#### Scenario: Cancel while output is backpressured

- **WHEN** a request is cancelled after its output write starts
- **THEN** a sibling event or cancellation acknowledgement follows complete JSON lines
- **AND** unrelated requests retain valid streams

### Requirement: Native Responses WebSocket frames are interpreted in Rust

When a native WebSocket request opts into Responses interpretation, the helper
MUST classify JSON object frames and embed their original JSON payload in IPC.
The Python adapter MUST reuse the payload decoded by the IPC reader for native
WebSocket request matching and event processing. The helper MUST preserve
original frame text, JSON numbers, duplicate-key precedence and the request
identifier. Invalid JSON, non-object frames, frames larger than 1 MiB (1,048,576
UTF-8 bytes), and objects that cannot be classified losslessly MUST retain opaque
delivery. Live WebSockets MUST remain opaque.

A string `type` MUST take precedence, including an empty string; otherwise an
object-valued `error` MUST classify as `error`. The native WebSocket boundary MUST
preserve aliases unchanged, matching the existing WebSocket relay. Error
conversion, HTTP-specific normalization, request matching, lifecycle and retry
policy MUST remain with their existing Python consumers. Rust MUST NOT close a
socket merely because it classified a terminal event.

#### Scenario: Canonical delta retains request ownership

- **WHEN** a native Responses delta includes a response id and sequence number
- **THEN** Python uses the decoded payload and Rust event type without reparsing
- **AND** request matching, sequence tracking and downstream text remain unchanged

#### Scenario: Alias preserves WebSocket relay behavior

- **WHEN** a WebSocket frame uses a legacy event alias
- **THEN** Rust preserves its original type and text
- **AND** any HTTP-specific normalization remains at the HTTP conversion boundary

#### Scenario: Numeric values remain exact

- **WHEN** a frame carries an integer larger than 64 bits or a float
- **THEN** its original numeric tokens reach the Python IPC decoder without Rust numeric conversion
- **AND** downstream text is identical to the upstream text

#### Scenario: Error envelope retains Python ownership

- **WHEN** a typeless frame contains an object error or has an explicit string type
- **THEN** Rust applies string-type precedence before classifying a typeless error
- **AND** Python retains public error conversion and retry policy with the original payload

#### Scenario: HTTP bridge preserves its existing framing semantics

- **WHEN** a native frame is a single-line object beginning with an opening brace
- **THEN** the HTTP bridge reuses its decoded payload and event type
- **AND** other text shapes retain the existing SSE-field parsing behavior

#### Scenario: Live WebSocket remains opaque

- **WHEN** a native Live WebSocket receives text or binary frames
- **THEN** the helper emits the existing opaque WebSocket events

### Requirement: Upstream streaming requests are bounded before the first response byte

A streaming upstream request MUST reach response headers within the effective stream
idle timeout. The bound applies from the moment the request is issued, so a connection
that is established but never answered fails on the same budget as a stream that stops
mid-flight.

Exceeding the bound MUST be reported with the existing `stream_idle_timeout` error code
and failure detail, MUST release every resource the attempt holds — including the
per-session response-create gate and any account lease — and MUST be eligible for the
same retry and failover handling as an idle timeout observed after the first byte.

Non-streaming control calls (token refresh, usage fetch, compaction) keep their own
timeouts and are unaffected.

#### Scenario: Established connection never returns response headers

- **GIVEN** an upstream connection that completes its TCP and TLS handshake
- **AND** the peer sends no response headers
- **WHEN** the effective stream idle timeout elapses
- **THEN** the attempt fails with `stream_idle_timeout`
- **AND** the failure is recorded before the request budget would have expired

#### Scenario: Response headers inside the bound stream normally

- **GIVEN** an upstream request whose response headers arrive before the idle timeout
- **WHEN** the stream then produces events with gaps shorter than the idle timeout
- **THEN** the request completes normally
- **AND** the pre-header bound does not truncate the stream

### Requirement: Routed streaming upstream responses are released when the consumer stops before EOF

When an upstream streaming request is issued through a resolved upstream proxy route,
the response body is consumed unbuffered and the consumer routinely stops before the
body reaches EOF: on the terminal stream event, on the stream idle timeout, on
cancellation, on downstream disconnect, and when the response is mapped to an error
before the body is drained. On every such exit the proxy MUST release or close the
upstream response object before it closes the per-stream client that owns the
connection, so the connection is returned or closed synchronously and no connection
object is left to be finalized by the garbage collector.

The release MUST work for every response shape the routed path can receive: an
aiohttp response (`release()`), a native egress response (`aclose()`), and a buffered
or duck-typed response that exposes neither (no-op). Responses obtained through a
SOCKS route MUST release the wrapped response before closing the private session that
carried it.

Release MUST run only after the last event block has been yielded to the consumer and
MUST NOT change the forwarded bytes, the error mapping, the retry classification, or
the cancellation semantics of the stream.

#### Scenario: Terminal event arrives while upstream holds the connection open

- **GIVEN** a routed HTTP stream whose upstream emits `response.completed` and then keeps the connection open
- **WHEN** the proxy stops reading on the terminal event
- **THEN** the upstream response is released before the per-stream client is closed
- **AND** no `Unclosed connection` event is reported to the event loop exception handler after a full garbage collection
- **AND** the forwarded event blocks are byte-identical to the upstream frames

#### Scenario: Stream idle timeout

- **GIVEN** a routed HTTP stream whose upstream goes silent after the first event
- **WHEN** the stream idle timeout elapses
- **THEN** the synthetic `stream_idle_timeout` failure event is yielded as before
- **AND** the upstream response is released before the per-stream client is closed

#### Scenario: Cancellation or downstream disconnect mid-stream

- **GIVEN** a routed HTTP stream that is cancelled, or whose consumer calls `aclose()`, while a body read is pending
- **WHEN** the stream generator unwinds
- **THEN** the upstream response is released before the per-stream client is closed
- **AND** the cancellation propagates to the caller unchanged

#### Scenario: Error status mapped before the body is drained

- **GIVEN** a routed HTTP stream whose upstream answers with a non-2xx status
- **WHEN** the proxy raises the mapped `ProxyResponseError`
- **THEN** the upstream response is released before the per-stream client is closed

#### Scenario: Response without a release method

- **GIVEN** a routed response object that exposes neither `release()`, `close()` nor `aclose()`
- **WHEN** the stream ends
- **THEN** teardown is a no-op for the response and the stream result is unchanged

### Requirement: Native HTTP response compression remains representation-consistent

Native HTTP egress MUST preserve the caller's compression-negotiation presence and value when constructing the upstream request. When a supported response coding is negotiated, the helper MUST decode the upstream response before relaying its body to the Python adapter. Headers relayed with the decoded body MUST describe the decoded representation and MUST NOT retain the stale upstream `Content-Encoding` or the encoded entity's `Content-Length`. This behavior MUST apply without changing direct or account-routed request ownership, replay policy, or streaming delivery.

#### Scenario: Native helper relays a gzip JSON response

- **GIVEN** a direct or account-routed native HTTP request advertises `Accept-Encoding: gzip`
- **WHEN** the upstream responds with a gzip-encoded JSON or SSE body and encoded-entity headers
- **THEN** the helper relays the decoded representation bytes
- **AND** the relayed headers omit the stale gzip content encoding and encoded content length
- **AND** the existing JSON or SSE adapter can consume the original representation

#### Scenario: Inbound request omits compression negotiation

- **GIVEN** a direct or account-routed native HTTP request has no `Accept-Encoding` header
- **WHEN** the helper constructs the upstream request
- **THEN** the upstream request MUST also omit `Accept-Encoding`
- **AND** the helper MUST NOT synthesize a response-coding advertisement

#### Scenario: Inbound request includes compression negotiation

- **GIVEN** a direct or account-routed native HTTP request includes an `Accept-Encoding` value using gzip, deflate, Brotli, or zstd
- **WHEN** the helper constructs and executes the upstream request
- **THEN** it MUST forward the inbound `Accept-Encoding` value unchanged
- **AND** the native HTTP client MUST have decoders enabled for gzip, deflate, Brotli, and zstd
- **AND** a response using any enabled coding MUST be decoded before relay to Python

### Requirement: Native helper stream events are bounded per request by a byte budget

Events the native egress helper emits for one request MUST be buffered between the shared helper reader and that request's consumer in a per-request queue bounded by a queued-payload byte budget (32 MiB) together with an event-count cap (4096). Queued bytes MUST be released as the consumer drains. A burst of small framed events or a body made of large chunks that fits the byte budget MUST NOT fail a consumer that is still draining. Only when a request's queue exceeds its budget MAY the reader fail that request with `consumer_backpressure`, drop its queued events, cancel the helper-side request, and continue serving other requests.

#### Scenario: Burst of small framed events drains without failure

- **GIVEN** the helper has 2000 small body events for one request buffered in its output pipe
- **WHEN** the consumer starts reading after the burst landed
- **THEN** the consumer receives the complete body and the request is not failed

#### Scenario: A consumer that stops draining is bounded by bytes

- **GIVEN** a request whose consumer does not read while the helper emits 48 chunks of 1 MiB
- **WHEN** the queued payload exceeds 32 MiB
- **THEN** that request fails with `consumer_backpressure`
- **AND** other requests on the same helper keep being served

### Requirement: Subscription Responses synthesize final routing hints

Subscription-account Responses HTTP egress, transient WebSocket handshakes,
persistent WebSocket handshakes and HTTP fallback MUST synthesize
`x-codex-routing-hint: model=<model>;tier=<tier>` from the final normalized
model and service tier. With no tier the hint MUST contain only `model=<model>`.
A preconnect without a request model MUST omit the hint. Reusing an open
WebSocket MUST NOT reconnect solely to replace its handshake hint. Synthesis
MUST NOT alter request bodies, entitlement checks or actual response tiers.

#### Scenario: Fast account request through either public API
- **WHEN** a subscription request selects Fast through the backend or v1 route
- **THEN** its outbound body and synthesized hint MUST use priority
- **AND** caller API-key authentication MUST NOT disable hint synthesis

#### Scenario: WebSocket fallback retains request routing
- **WHEN** a subscription WebSocket attempt falls back to HTTP
- **THEN** the HTTP hint MUST use the same final model and service tier

#### Scenario: Custom provider does not gain a backend hint
- **WHEN** a request is dispatched to a non-subscription model source
- **THEN** no Codex-backend routing hint MUST be synthesized

### Requirement: Subscription compaction propagates routing hints

Compaction egress for proxy-routed subscription client requests MUST synthesize
`x-codex-routing-hint: model=<model>;tier=<tier>` from the final normalized request.
When no tier remains after policy enforcement, the hint MUST contain only
`model=<model>`. Eligibility MUST use selected subscription provenance, including
when the optional ChatGPT account-ID header is absent. Hint synthesis MUST NOT
change compaction input, usage, retries, or the response's actual service tier.

#### Scenario: Fast compaction uses the canonical tier
- **WHEN** a subscription compaction request selects the Fast alias
- **THEN** its outbound body and hint MUST both use `priority`

#### Scenario: Ultrafast compaction preserves the requested tier
- **WHEN** an eligible subscription compaction request selects `ultrafast`
- **THEN** its outbound body and hint MUST both use `ultrafast`

#### Scenario: Prohibited Fast does not leak through the hint
- **WHEN** policy removes a subscription compaction request's Fast tier
- **THEN** its outbound body MUST omit that tier
- **AND** its hint MUST contain only the final model

#### Scenario: Non-subscription compaction does not synthesize a hint
- **WHEN** compaction transport is invoked without subscription provenance
- **THEN** it MUST NOT synthesize or forward a routing hint

### Requirement: Account circuit breakers are constructed unconditionally and used per the dashboard toggle

The upstream client MUST create (and keep) a per-account circuit breaker regardless of the `circuit_breaker_enabled` toggle, and MUST decide per request whether to consult it — pre-call check, half-open probe, success and failure recording — from the effective `circuit_breaker_enabled` value of the dashboard-settings snapshot the request path resolved. Because the client is reached without a settings argument, every request path that reaches the client with an account (stream, compact, WebSocket connect, codex control, thread goal, transcription, warmup fan-out, and the background limit-warmup, quota-planner warmup and automation callers) MUST bind the resolved toggles to its task after taking its snapshot, MUST rebind them before every upstream attempt whose generator may have been handed to another task since (a streaming request that yielded a capacity keepalive before opening upstream), and the client MUST read them from the task; a task with no binding MUST fall back to the process environment value. Turning the toggle on or off in the dashboard MUST take effect on the next upstream attempt without a restart, and turning it off MUST NOT destroy or reset existing breaker state.

#### Scenario: Toggle turned off while a breaker is open

- **GIVEN** the dashboard toggle is on and repeated upstream server errors have opened an account's breaker
- **WHEN** an operator turns the circuit breaker off in the dashboard and the next request for that account is attempted
- **THEN** the attempt is not rejected by the open breaker
- **AND** the breaker object still exists and still reports open

#### Scenario: Toggle turned on without a restart

- **GIVEN** the process started with `CODEX_LB_CIRCUIT_BREAKER_ENABLED=false`
- **WHEN** an operator turns the circuit breaker on in the dashboard and an account fails with upstream server errors up to the fixed threshold
- **THEN** the account's breaker opens and the following attempt is rejected with the breaker-open error

### Requirement: Upstream connect timeout is dashboard-managed

The upstream connect timeout applied to every outbound upstream request — Responses streams, thread-goal and control calls, compaction, transcription, file uploads, upstream WebSocket handshakes and the HTTP bridge owner forward — MUST be the effective `upstream_connect_timeout_seconds` resolved as code default < environment < dashboard: a non-NULL `dashboard_settings.upstream_connect_timeout_seconds` overrides `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS`. Consumers MUST read it from the `SettingsCache` snapshot bound at the request or connection entry point (never from the database on the request path); per-attempt overrides that clamp the connect timeout to a remaining budget keep applying on top of the effective value. `PUT /api/settings` MUST reject, with `400 timeout_invariant_violation`, a connect timeout that would exceed the effective proxy, compact or transcription request budget, because such a value is clamped to the budget and can never be honoured.

#### Scenario: Dashboard connect timeout overrides startup environment

- **GIVEN** `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS=8` and an operator stores `3` through `PUT /api/settings`
- **WHEN** a new request opens an upstream connection on any replica
- **THEN** the aiohttp connect (`sock_connect`) timeout is 3 seconds
- **AND** `GET /api/settings` reports `upstreamConnectTimeoutSeconds: 3` with `provenance.upstream_connect_timeout_seconds.source = "dashboard"`

#### Scenario: Connect timeout above a budget is rejected

- **GIVEN** the effective transcription request budget is 120 seconds
- **WHEN** the operator sends `PUT /api/settings` with `upstreamConnectTimeoutSeconds: 130`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `upstream-connect-within-transcription-budget`
- **AND** the same `PUT` with `transcriptionRequestBudgetSeconds: 150` alongside is accepted

#### Scenario: Startup warns when the environment is shadowed

- **GIVEN** `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS` is set in the environment and the dashboard column is non-NULL
- **WHEN** the process starts
- **THEN** one WARN names the shadowed variable and points at the dashboard
- **AND** no WARN is logged when the variable is unset or the column is NULL

### Requirement: Rust owns recognized native HTTP Responses completion

The native adapter MUST require `http_responses_completion_v1` before dispatch.
For interpreted HTTP Responses streams, Rust MUST stop reading the upstream body
after classifying `response.completed`, `response.failed`, or
`response.incomplete`, deliver the entire terminal event, and release the body
without waiting for EOF. It MUST ignore subsequent bytes of that exchange.
The final fragment of every recognized terminal MUST carry `stream_complete=true`.
All other fragments MUST omit the completion marker or set it to false.
An absent completion marker MUST mean false. Python MUST validate this marker and retire that request without sending cancel
once its complete terminal block has been assembled. Malformed or truncated
fragments MUST fail without replay. Cancellation before completion and bounded
queue overflow MUST still release that request without invalidating its peers.

Frames that do not classify as one of these three terminal types, including bare
`error` events and typeless error envelopes, MUST retain Python's existing
SDK-dependent normalization, termination decisions, and cancellation cleanup.
Rust MUST NOT mark such a frame complete solely because it contains an error.

#### Scenario: Upstream remains open after a terminal

- **WHEN** a direct or routed native HTTP stream sends a recognized terminal and leaves its body open
- **THEN** the full terminal reaches the consumer and Rust releases the upstream body
- **AND** Python sends no cancellation command for normal completion

#### Scenario: Fragmented terminal followed by invalid bytes

- **WHEN** a recognized terminal spans multiple IPC fragments and is followed by an oversized event
- **THEN** all terminal fragments are delivered before completion
- **AND** the later event causes no size failure or additional output

#### Scenario: Cancellation or malformed completion

- **WHEN** a consumer cancels before completion or receives an invalid completion marker
- **THEN** its request is cleaned up without replay
- **AND** another request on the same helper remains usable

#### Scenario: Other lifecycle owners remain active

- **WHEN** a stream uses raw HTTP, uninterpreted SSE, compact collection, a Python fallback, or a persistent WebSocket
- **THEN** that transport retains its existing termination protocol

#### Scenario: Typeless error normalization depends on the SDK contract

- **WHEN** a native HTTP stream receives a typeless error envelope followed by a recognized terminal
- **THEN** without SDK-contract enforcement, the envelope is delivered unchanged and the stream continues to the recognized terminal
- **AND** with SDK-contract enforcement, Python normalizes the envelope to `response.failed` and terminates through its existing cleanup path
- **AND** both modes preserve the corresponding Python fallback result

### Requirement: Direct usage queries prefer native egress with explicit ownership

After the existing route-or-direct authorization check, a usage GET without a
resolved route or explicit Python RetryClient MUST prefer the discovered native
helper. An explicit Python client MUST bypass native discovery. Routed usage
MUST retain CodexClient ownership. Native requests MUST preserve the usage URL,
authentication/account/request-id headers, default compression negotiation and
effective total timeout, and MUST
use the existing native HTTP environment-proxy resolver. Python MUST retain
payload validation, public error mapping and retry policy.

#### Scenario: Explicit client and route retain precedence

- **WHEN** a usage call supplies a resolved route or a direct Python client
- **THEN** it uses that route or client without direct native discovery
- **AND** an unauthorized direct call fails before any helper discovery or network call

#### Scenario: Missing helper permits only initial fallback

- **WHEN** no helper is found or the first native request raises helper-unavailable before dispatch
- **THEN** the original Python transport handles the GET
- **AND** protocol incompatibility, body failure, or helper loss after a prior attempt MUST NOT select Python fallback

### Requirement: Native usage preserves direct retry and response failure semantics

Native usage GETs MUST use the existing direct retryable HTTP statuses, maximum
attempt count and one-based ExponentialRetry delay calculation. Retryable status
responses MUST be closed before backoff without waiting for their body. Native
transport failures before response headers MAY use the remaining GET attempts;
protocol failures and final-response body failures MUST NOT be retried. Native
transport/protocol failures MUST surface as UsageFetchError with status 0.

For a fully read response, non-JSON error text and non-object JSON MUST retain
the existing direct Python error mapping, including charset and empty-body
handling. Compressed responses MUST be decoded as with the default Python client.
Malformed success payloads MUST retain
the 502 invalid-usage-payload result. Cancellation MUST propagate after exchange
cleanup and MUST NOT invalidate other requests sharing the helper.

#### Scenario: Status retry does not depend on body completion

- **WHEN** a retryable usage response sends headers but stalls its body and an attempt remains
- **THEN** its exchange is closed and the next native GET follows the existing backoff
- **AND** the same effective per-attempt timeout and attempt cap apply

#### Scenario: Response-body failure remains a transport error

- **WHEN** the selected response body is truncated or times out
- **THEN** the call raises UsageFetchError with status 0 without retry or Python fallback
- **AND** fully read plain-text error responses preserve their text instead

#### Scenario: Encoded responses match the default Python client

- **WHEN** the upstream returns compressed JSON or a body with an explicit charset
- **THEN** native usage decodes it and produces the same payload or error as direct Python usage
- **AND** empty, whitespace-only and non-object JSON bodies retain the Python error mapping

#### Scenario: Cancellation and helper exit are isolated

- **WHEN** one usage query is cancelled before headers or while reading the body
- **THEN** its exchange is retired and another query can use the same helper
- **WHEN** the helper exits during a body read
- **THEN** that query fails without replay and a subsequent independent query can start a fresh helper
