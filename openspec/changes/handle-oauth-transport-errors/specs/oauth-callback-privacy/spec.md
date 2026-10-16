## ADDED Requirements

### Requirement: OAuth account-add reachability failures finalize without leaking handshake credentials

When an outbound transport failure prevents the auth server from being reached, both account-add flows SHALL finalize with a usable error instead of an unhandled server error, and neither the response nor the log MAY contain the authorization code, the `state` token, the PKCE verifier, tokens, or proxy credentials.

For a browser callback, the loopback callback handler MUST finalize the flow as `error` with the sanitized message — writing it durably so `/api/oauth/status` reports `error` and the dashboard stops polling — and MUST answer the callback with the OAuth error page rather than a generic server error response. For a device flow, a failure to obtain the device code MUST answer `POST /api/oauth/start` with the repo's dashboard error envelope and a non-500 status, and a failure during device-token polling MUST record the terminal `error` status rather than leaving the flow `pending`.

#### Scenario: Browser callback cannot reach the auth server

- **GIVEN** a pending browser OAuth flow and a callback carrying a valid `code` and `state`
- **WHEN** the token exchange cannot reach the auth server
- **THEN** the callback response is the OAuth error page (not an unhandled server error)
- **AND** the flow status becomes `error` with the reachability message, so status polling reports the error and stops
- **AND** neither the authorization code nor the state token appears in the response body or in the logs

#### Scenario: Device start cannot reach the auth server

- **GIVEN** a dashboard `POST /api/oauth/start` with `forceMethod=device`
- **WHEN** the device-code request cannot reach the auth server
- **THEN** the response uses the dashboard error envelope with a non-500 status
- **AND** the reported status is the sanitized reachability error

#### Scenario: Device-token polling cannot reach the auth server

- **GIVEN** a started device flow whose poller is exchanging the device code
- **WHEN** the poll cannot reach the auth server
- **THEN** the flow is finalized as `error` with the sanitized message
- **AND** the poll task does not die with an unretrieved exception
