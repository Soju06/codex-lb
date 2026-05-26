## ADDED Requirements

### Requirement: OAuth add-account with proxy validates before account activation

The service MUST handle account adds through the OAuth dashboard flow
(browser, manual-callback, or device-code) together with a SOCKS5 proxy
configuration by performing the end-to-end proxy probe BEFORE inserting or
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
