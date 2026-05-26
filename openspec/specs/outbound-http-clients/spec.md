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

### Requirement: Browser OAuth redirect URI uses the registered callback

Browser OAuth start MUST use the configured `oauth_redirect_uri` unchanged
for the authorize `redirect_uri` and response `callbackUrl`. The token
exchange for that OAuth attempt MUST reuse the same redirect URI. The
dashboard request host MUST NOT rewrite the OAuth redirect URI.

#### Scenario: remote dashboard host does not rewrite browser OAuth callback
- **WHEN** a browser OAuth flow starts from a dashboard request whose host is
  `dashboard.example.test:2455`
- **AND** `oauth_redirect_uri` is `http://localhost:1455/auth/callback`
- **THEN** the authorize URL uses
  `redirect_uri=http://localhost:1455/auth/callback`
- **AND** the start response includes that same callback URL
- **AND** the authorization-code token exchange uses that same redirect URI

### Requirement: Account-bound outbound calls use the per-account egress client

Account-bound calls MUST route every outbound HTTP or WebSocket call made
on behalf of an account with a SOCKS5 proxy configured through that proxy.
When an account has no proxy configured, account-bound calls MUST
use a dedicated per-account direct egress client and MUST NOT share TCP /
TLS connections with other accounts.

The following call sites are considered "account-bound" and MUST honor this
rule:

- OAuth token refresh
- Codex `responses` HTTP path
- Codex `responses` WebSocket handshake and transport
- Account model fetcher
- Account usage fetcher
- Account-scoped file uploads/downloads

The following call sites are considered "non-account" and MUST continue to
use the shared global client unchanged:

- GitHub release / version check
- Dashboard-internal HTTP calls
- The OAuth login bootstrap flow that runs before any account exists

#### Scenario: Account with a configured proxy egresses through the proxy
- **WHEN** the account has `proxy_host` set
- **AND** the service performs an OAuth token refresh, a Codex `responses`
  HTTP request, a Codex `responses` WebSocket handshake, a model/usage
  fetch, or a files call for that account
- **THEN** the underlying `aiohttp.ClientSession` MUST be backed by a
  `ProxyConnector` whose proxy host, port, credentials, and DNS mode match
  the stored proxy configuration

#### Scenario: Account without a proxy uses dedicated direct egress
- **WHEN** the account has no `proxy_host`
- **AND** the service performs any account-bound outbound call
- **THEN** the call MUST construct or reuse the account's dedicated direct
  outbound client
- **AND** the call MUST NOT use the shared global outbound client

#### Scenario: Non-account outbound calls keep using the shared global client
- **WHEN** the service performs the GitHub release check, a dashboard-
  internal HTTP call, or the OAuth login bootstrap flow
- **THEN** the call MUST use the shared global outbound client regardless
  of any account proxy configuration

### Requirement: Per-account token refresh jitter

The token refresh schedule MUST apply a deterministic per-account offset
to the configured refresh interval so that accounts onboarded on the
same day do not refresh on the same day. The offset MUST be derived
from `account_id` only — not from `last_refresh` — so a given account
always lands at the same point inside its window. The offset MUST be in
the range
`[-account_token_refresh_jitter_hours, +account_token_refresh_jitter_hours]`.

When `account_id` is not provided to the schedule check, the service
MUST fall back to the un-jittered `token_refresh_interval_days`
behavior.

#### Scenario: Same account always lands at the same point in its window
- **WHEN** the refresh schedule check is evaluated twice for the same
  `account_id` with the same `last_refresh`
- **THEN** both calls observe the same effective threshold

#### Scenario: Distinct accounts get distinct offsets
- **WHEN** the refresh schedule check is evaluated for two different
  `account_id`s with the same `last_refresh`
- **THEN** the two effective thresholds differ

#### Scenario: Offset is bounded by the configured jitter window
- **WHEN** `account_token_refresh_jitter_hours` is `H`
- **THEN** every per-account offset MUST be in
  `[-H * 3600, +H * 3600]` seconds

### Requirement: Outbound calls strip device and installation identifier headers

Outbound calls MUST strip every header in the device-identifier deny
set before forwarding to upstream. The deny set covers, at minimum:
`oai-device-id`, `oai-did`, `x-oai-device-id`, `x-openai-device-id`,
`oai-installation-id`, `x-oai-installation-id`, and
`x-openai-installation-id`. Header name matching MUST be
case-insensitive.

The deny set MUST be enforced in both filtering paths: the broad
forward path used by Codex `responses` HTTP and WebSocket, and the
explicit allow-list paths used by `/files` and `/transcribe` (even
when an entry would otherwise match the `x-openai-` / `x-codex-`
allow-list prefix).

#### Scenario: oai-device-id is stripped from a /responses request
- **WHEN** a Codex client sends `oai-device-id: device-abc` to the
  proxy
- **THEN** the upstream `/responses` HTTP request MUST NOT include
  `oai-device-id`

#### Scenario: x-openai-device-id is stripped from a /files request
- **WHEN** a Codex client sends `x-openai-device-id: device-abc` to
  the proxy
- **THEN** the upstream `/files` request MUST NOT include the header,
  even though the `x-openai-` prefix is otherwise allow-listed

#### Scenario: Header name matching is case-insensitive
- **WHEN** the inbound header is named `Oai-Device-Id`,
  `OAI-DEVICE-ID`, or any other case variant
- **THEN** the header MUST be stripped on the same code path as
  `oai-device-id`
