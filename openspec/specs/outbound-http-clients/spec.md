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

