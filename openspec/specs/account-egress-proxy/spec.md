# account-egress-proxy Specification

## Purpose

Define how each ChatGPT account's outbound traffic is optionally routed
through a per-account SOCKS5 proxy so operators running multiple accounts
can give each one a distinct egress IP.

## Requirements

### Requirement: Account proxy configuration shape

Each account MUST have at most one SOCKS5 egress proxy configuration. The
configuration MUST be stored as structured fields on the `accounts` row:

- `proxy_host` (string, required when a proxy is configured)
- `proxy_port` (integer, 1–65535, required when a proxy is configured)
- `proxy_username` (string, optional, plaintext)
- `proxy_password_encrypted` (encrypted bytes, optional, encrypted at rest
  with the same `TokenEncryptor` used for OAuth tokens)
- `proxy_remote_dns` (boolean, default `true`; when `true` SOCKS5 connects
  resolve hostnames at the proxy — `socks5h` semantics)
- `proxy_label` (string, optional operator-facing label)

A row with `proxy_host IS NULL` represents the "no proxy" state and MUST be
the default for new and existing accounts.

#### Scenario: New account defaults to no proxy and remote DNS true
- **WHEN** a new account is created
- **THEN** `proxy_host`, `proxy_port`, `proxy_username`,
  `proxy_password_encrypted`, and `proxy_label` are `NULL`
- **AND** `proxy_remote_dns` is `true`

#### Scenario: Stored proxy password is encrypted at rest
- **WHEN** an account is configured with a proxy that has a password
- **THEN** the database stores the password in `proxy_password_encrypted`
  encrypted via the same `TokenEncryptor` used for OAuth tokens
- **AND** no plaintext password is persisted

### Requirement: Save-time end-to-end proxy probe

The service MUST validate every proposed proxy configuration with an
end-to-end HTTPS probe before persisting it. The probe MUST construct a
one-shot `ProxyConnector` from the proposed configuration and perform a
real OAuth token refresh against the upstream OAuth endpoint using the
account's current refresh token. The probe MUST classify the outcome into
one of: `ok`, `proxy_connect`, `proxy_auth`, `tls`, `upstream_status`,
`invalid_response`, `timeout`. Only `ok` MUST persist the configuration.

#### Scenario: Successful probe persists the proxy
- **WHEN** an operator submits a valid proxy configuration
- **AND** the end-to-end OAuth refresh through the proxy returns 2xx
- **THEN** the proxy configuration is persisted on the account
- **AND** the API response includes the new `AccountProxySummary`

#### Scenario: Probe failure rejects the proxy with a typed reason
- **WHEN** an operator submits a proxy configuration that the probe
  classifies as `proxy_connect`, `proxy_auth`, `tls`, `upstream_status`,
  `invalid_response`, or `timeout`
- **THEN** the proxy configuration is NOT persisted
- **AND** the API responds 422 with `error.code=proxy_probe_failed` and
  `error.reason` equal to the probe classification

#### Scenario: Import with proxy validates before account activation
- **WHEN** an operator imports an `auth.json` file together with a proxy
  configuration
- **THEN** the service MUST perform the end-to-end proxy probe before
  inserting or updating the account row
- **AND** a probe failure MUST return 422 with `error.code=proxy_probe_failed`
  and MUST NOT persist the imported account
- **AND** a successful probe MUST persist the account, proxy fields, and any
  rotated OAuth tokens atomically before any import-time usage refresh runs
- **AND** the account's cached egress client MUST be invalidated before any
  import-time usage refresh runs

### Requirement: OAuth add-account with proxy validates before account activation

The service MUST handle account adds through the OAuth dashboard flow (browser,
manual-callback, or device-code) together with a SOCKS5 proxy configuration,
by performing the end-to-end proxy probe BEFORE inserting or
updating the account row. The OAuth attempt MUST signal "proxy expected" at
`POST /api/oauth/start` time via an `expect_proxy=true` flag and MUST include
the start-time proxy configuration on that start request. When that flag is
set, server-side OAuth bootstrap/token-exchange calls for that attempt MUST
use the start-time SOCKS5 proxy with the codex TLS profile.
Token-arrival sites MUST hold the acquired OAuth tokens in transient in-memory
state and MUST NOT persist the account until the operator submits a proxy
configuration via `POST /api/oauth/complete`.
This requirement covers codex-lb server-side OAuth calls, proxy probes, and
post-persistence account egress; the operator's own browser/device navigation
to upstream OpenAI/Codex sign-in pages is outside backend egress control and
MUST NOT be treated as account activation or account traffic through codex-lb.
That completion-time proxy configuration MAY be a correction after a previous
probe failure, but it MUST still be probed before account persistence. A probe
failure MUST return 422 `proxy_probe_failed` with a typed reason and MUST NOT
persist the account. A successful probe MUST persist the account, the
completion-time proxy configuration, and the rotated OAuth tokens atomically,
and MUST invalidate the account's cached egress client before any post-add
usage refresh runs.

The held tokens MUST expire after a bounded window (default 10 minutes) so
the service does not retain unpersisted authenticated state indefinitely.
Closing the dashboard's OAuth dialog (which calls `start_oauth` again or
otherwise resets the OAuth state store) MUST also drop the held tokens.

#### Scenario: OAuth-with-proxy persists atomically on successful probe
- **WHEN** an operator starts an OAuth attempt with `expect_proxy=true`
- **AND** completes the upstream sign-in
- **AND** submits a valid proxy configuration via `POST /api/oauth/complete`
- **AND** the end-to-end OAuth refresh through the proxy returns 2xx
- **THEN** the service persists the account, proxy fields, and rotated
  tokens atomically before any usage refresh runs
- **AND** the API response includes the new `AccountProxySummary`
- **AND** the account's cached egress client is invalidated before any
  post-add usage refresh runs

#### Scenario: OAuth-with-proxy rejects probe failure without persisting
- **WHEN** an operator submits a proxy configuration through
  `POST /api/oauth/complete` after a successful upstream sign-in
- **AND** the probe classifies the outcome as `proxy_connect`, `proxy_auth`,
  `tls`, `upstream_status`, `invalid_response`, or `timeout`
- **THEN** the service responds 422 with `error.code=proxy_probe_failed`
  and `error.reason` equal to the probe classification
- **AND** the account is NOT persisted
- **AND** the held OAuth tokens remain in transient state so the operator
  can retry `POST /api/oauth/complete` with a corrected proxy without
  redoing the upstream sign-in

#### Scenario: Held OAuth tokens expire after the bounded window
- **WHEN** an OAuth attempt with `expect_proxy=true` has reached
  `tokens_ready` state
- **AND** more than 10 minutes elapse without a successful
  `POST /api/oauth/complete`
- **THEN** the service drops the held tokens, transitions OAuth state to
  `error`, and requires the operator to restart the OAuth attempt

#### Scenario: OAuth without proxy preserves immediate persistence
- **WHEN** an operator starts an OAuth attempt with `expect_proxy=false`
  (or unset)
- **AND** completes the upstream sign-in
- **THEN** the service persists the account immediately on token arrival as
  before, without requiring `POST /api/oauth/complete` for persistence
- **AND** the account is created without a proxy configuration

### Requirement: Account proxy summary never leaks the password

Read APIs that return account state MUST surface an `AccountProxySummary`
when a proxy is configured. The summary MUST include `host`, `port`,
`label`, `remote_dns`, `has_password: bool`, and `last_validated_at`. The
encrypted or plaintext password MUST NEVER appear in any read response,
audit log entry, structured log line, or metric label.

#### Scenario: Read summary exposes connection metadata without the password
- **WHEN** the dashboard reads an account that has a proxy configured with
  a password
- **THEN** the response includes `host`, `port`, `label`, `remote_dns`,
  `has_password=true`, and `last_validated_at`
- **AND** the response does NOT include the password in any form

#### Scenario: Audit log records proxy mutations without secrets
- **WHEN** an operator sets or clears an account proxy
- **THEN** an audit log entry `account_proxy_set` or
  `account_proxy_cleared` is recorded
  with `host`, `port`, and `label`
- **AND** no entry contains the password in any form

### Requirement: Per-account egress client lifecycle

The service MUST maintain at most one pooled `aiohttp.ClientSession` per
account-bound egress, regardless of whether the account has a SOCKS5
proxy configured. The egress fingerprint MUST be a discriminated union
of:

- `AccountProxyConnection` — when a SOCKS5 proxy is configured, the
  session MUST be backed by an `aiohttp_socks.ProxyConnector` whose
  `rdns` argument matches the stored `proxy_remote_dns` flag.
- `DirectEgress` — when no proxy is configured, the session MUST be
  backed by a dedicated `aiohttp.TCPConnector` for that account. Two
  distinct accounts MUST NOT share the same TCP / TLS connection on
  the direct path.

Genuinely non-account outbound calls (the OAuth login bootstrap, the
release / version check, dashboard internals) MUST continue to use
the shared global client.

The session MUST be retired and replaced when the account's egress
fingerprint changes (proxy added, edited, or cleared), when the
account is deactivated by the runtime failure tracker, or on global
shutdown.

#### Scenario: Direct account gets a dedicated session
- **WHEN** an account has no proxy configured and a non-empty
  `account_id` is passed to the egress acquire helper
- **THEN** the registry MUST construct a per-account direct managed
  client and cache it
- **AND** the session MUST NOT be the shared global session

#### Scenario: Two distinct direct accounts do not share a session
- **WHEN** two different accounts each acquire an egress without
  proxies configured
- **THEN** the two managed clients MUST be distinct cache entries
  with their own `aiohttp.ClientSession` and `TCPConnector`

#### Scenario: Configuration change retires the existing session

- **WHEN** an account's egress fingerprint changes (no-proxy →
  SOCKS5, SOCKS5 edit, or SOCKS5 → no-proxy)
- **THEN** any cached managed client built from the previous
  fingerprint MUST be retired and not used for subsequent calls

#### Scenario: Empty account_id keeps using the shared global client
- **WHEN** the egress acquire helper is called with an empty
  `account_id` (e.g. login bootstrap, release check)
- **THEN** the call MUST delegate to the shared global client and
  MUST NOT construct a per-account session

#### Scenario: Global shutdown drains per-account sessions
- **WHEN** the service shuts down
- **THEN** all per-account sessions (direct and proxy) MUST be
  closed before the shared global client closes

### Requirement: Codex TLS is the only account egress profile

Account-bound egress MUST use the codex TLS profile for HTTP, SSE, OAuth
probe, OAuth server-side, usage refresh, and WebSocket traffic. The codex profile
uses Python stdlib `ssl.create_default_context()` with ALPN pinned to
`http/1.1`, no browser impersonation, no per-account cipher rotation, and
one singleton `SSLContext` shared by all account-bound transports in the
process. The rule applies to both direct egress and explicit SOCKS5
proxy egress.

Non-account flows (login bootstrap, release / version check, dashboard
internals) MAY continue to use the default Python SSL behavior.

#### Scenario: Direct account transports use codex SSL
- **WHEN** an account makes outbound calls without an explicit SOCKS5
  proxy via HTTP and WebSocket transports
- **THEN** both transports MUST be configured with the singleton codex
  SSLContext

#### Scenario: Proxied account transports use codex SSL
- **WHEN** an account makes outbound calls through an explicit SOCKS5
  proxy via HTTP and WebSocket transports
- **THEN** both transports MUST be configured with the singleton codex
  SSLContext for the upstream-TLS hop

#### Scenario: Codex SSL is shared across accounts
- **WHEN** two accounts make account-bound outbound calls in the same
  process
- **THEN** both accounts MUST use the same cached codex SSLContext

### Requirement: Probe uses the codex TLS profile

The save-time probe MUST use the same TLS transport as the runtime:
aiohttp with the singleton codex SSLContext and ALPN pinned to
`http/1.1`.

#### Scenario: Save-time probe of an account proxy
- **WHEN** an operator saves a proxy configuration and the probe runs
- **THEN** the probe constructs an `aiohttp.ClientSession` with an
  `aiohttp_socks.ProxyConnector` whose `ssl=` is the singleton
  codex SSLContext
- **AND** the OAuth refresh observed by upstream during the probe
  emits the canonical OpenSSL ClientHello / ALPN that the saved
  account will continue to emit in steady state

### Requirement: Runtime proxy failures deactivate the account

The service MUST track proxy-level errors per account in a rolling window.
A "proxy-level error" MUST mean an exception of type `ProxyError`,
`ProxyConnectionError`, `ProxyTimeoutError` (from `python_socks._errors`
or `aiohttp_socks._errors`), `aiohttp.ClientProxyConnectionError`, or
`websockets.exceptions.ProxyError` / `websockets.exceptions.InvalidProxy`
raised on an account-bound call whose explicit per-account SOCKS5 proxy was
set. When the count of proxy-level errors for an account reaches the
configured threshold within the configured window, the service MUST atomically
transition the account to `DEACTIVATED` with
`deactivation_reason="proxy_unreachable"`, evict the cached per-account
session, and stop selecting that account for traffic. The service MUST NOT
automatically reactivate the account; only an explicit operator action
(clearing the proxy or reactivating) restores traffic.

The threshold and window MUST be configurable via
`account_proxy_failure_threshold` (default `3`) and
`account_proxy_failure_window_seconds` (default `60`). Non-proxy failures
(upstream HTTP 4xx/5xx, JSON decode errors, etc.) MUST NOT contribute to
the proxy failure counter.

#### Scenario: Threshold reached deactivates the account
- **WHEN** an account incurs `account_proxy_failure_threshold` proxy-level
  errors within `account_proxy_failure_window_seconds`
- **THEN** the account transitions to status `deactivated` with
  `deactivation_reason="proxy_unreachable"`
- **AND** the cached per-account session is evicted
- **AND** the account is no longer eligible for selection

#### Scenario: Non-proxy errors do not contribute to the counter
- **WHEN** an account-bound call fails with an upstream HTTP 5xx, an HTTP
  4xx, or a JSON decode error
- **THEN** the proxy failure counter for that account does NOT increment
- **AND** the existing circuit breaker / error handling still applies

#### Scenario: Window decay forgets old failures
- **WHEN** proxy-level errors occur outside the rolling
  `account_proxy_failure_window_seconds` window
- **THEN** they MUST NOT count toward the current threshold

### Requirement: Auth.json import/export does not carry proxy configuration

The `auth.json` import and export endpoints MUST NOT read or write the
account proxy configuration. The proxy configuration is operator-managed
through the dedicated `POST /api/accounts/{id}/proxy` and
`DELETE /api/accounts/{id}/proxy` endpoints only.

#### Scenario: Import of an auth.json does not modify proxy state
- **WHEN** an operator imports an `auth.json` for an account
- **THEN** any existing proxy configuration on that account is preserved
  unchanged
- **AND** no proxy fields are read from the `auth.json` payload

#### Scenario: Export of an account does not include proxy state
- **WHEN** an operator exports an account
- **THEN** the exported `auth.json` does NOT include any proxy fields
