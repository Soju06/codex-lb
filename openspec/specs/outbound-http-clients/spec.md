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
in-process cache that is refreshed by existing background refresh paths.

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

The upstream SSE event reader MUST NOT rescan previously scanned buffer bytes on each network read; framing cost MUST be linear in event size so a single large event (up to the configured event-size cap) cannot stall the shared event loop. Framing semantics MUST be unchanged: all separator forms (`\r\n\r\n`, `\n\n`, `\r\r`) are honored, including separators straddling read boundaries, and event-size limits and idle timeouts apply as before.

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

#### Scenario: Connector construction pins reuse settings

- **WHEN** the shared HTTP client initializes its direct TCP connectors
- **THEN** they are constructed with `keepalive_timeout >= 90` and `ttl_dns_cache >= 300`
