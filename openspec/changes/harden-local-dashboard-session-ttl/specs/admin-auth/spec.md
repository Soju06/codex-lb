# admin-auth — Local dashboard session TTL hardening (delta)

## MODIFIED Requirements

### Requirement: Dashboard password sessions use a configurable absolute lifetime

The system SHALL issue dashboard password-authenticated sessions with an absolute lifetime controlled by persisted dashboard settings. The default persisted lifetime SHALL be 1 year. Configured lifetimes at or below 30 days SHALL apply to newly issued dashboard password sessions by setting both the encrypted session expiry payload and the cookie `Max-Age` to the same value.

Configured lifetimes above 30 days SHALL apply only in standard dashboard auth mode when the request is socket-level local, or when an explicit loopback-host-header override is enabled and the request uses a loopback dashboard URL with no forwarded-client headers. When a request is non-loopback, proxy-aware, trusted-header authenticated, or bridge-originated without that explicit override, the system MUST issue the new session with a 12-hour effective lifetime instead of the longer configured lifetime. This fallback MUST NOT disable dashboard auth and MUST NOT rewrite the persisted setting.

#### Scenario: Direct loopback dashboard login receives the 1-year default

- **GIVEN** the configured dashboard session lifetime is the 1-year default
- **AND** the dashboard request is direct loopback in standard dashboard auth mode
- **WHEN** an admin successfully completes password authentication
- **THEN** the newly issued dashboard session expires after 1 year
- **AND** the cookie `Max-Age` is `31536000`

#### Scenario: Localhost-published bridge login requires explicit override

- **GIVEN** the configured dashboard session lifetime is the 1-year default
- **AND** the dashboard request uses a loopback dashboard URL but the socket peer is not loopback
- **WHEN** the explicit loopback-host-header override is disabled
- **THEN** the newly issued dashboard session expires after 12 hours

#### Scenario: Localhost-published bridge login can opt in to 1 year

- **GIVEN** the configured dashboard session lifetime is the 1-year default
- **AND** the dashboard request uses a loopback dashboard URL but the socket peer is not loopback
- **AND** the explicit loopback-host-header override is enabled
- **AND** no forwarded-client headers are present
- **WHEN** an admin successfully completes password authentication
- **THEN** the newly issued dashboard session expires after 1 year
- **AND** the cookie `Max-Age` is `31536000`

#### Scenario: Remote dashboard login falls back to 12 hours

- **GIVEN** the configured dashboard session lifetime is greater than 30 days
- **AND** the dashboard request is not direct loopback
- **WHEN** an admin successfully completes password authentication
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Proxy-aware dashboard login falls back to 12 hours

- **GIVEN** the configured dashboard session lifetime is greater than 30 days
- **AND** proxy headers are trusted or trusted-header dashboard auth is in use
- **WHEN** an admin successfully completes dashboard authentication
- **THEN** the newly issued dashboard session expires after 12 hours

#### Scenario: Shorter configured dashboard lifetime is preserved

- **GIVEN** the configured dashboard session lifetime is 2 hours
- **WHEN** an admin successfully completes password authentication
- **THEN** the newly issued dashboard session expires after 2 hours

#### Scenario: Existing dashboard sessions keep their original expiry

- **WHEN** an admin changes the configured dashboard session lifetime after a session cookie has already been issued
- **THEN** previously issued cookies continue to expire according to the expiry embedded in their encrypted payload
- **AND** only newly issued dashboard password sessions use the updated effective lifetime

### Requirement: Legacy default dashboard session TTL migration

The migration for this change MUST update `dashboard_settings.dashboard_session_ttl_seconds` from `43200` to `31536000` only for rows that still carry the legacy default value. Rows with any customized value MUST remain unchanged.

#### Scenario: Legacy default row migrates to 1 year

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 43200`
- **WHEN** the migration runs
- **THEN** the row has `dashboard_session_ttl_seconds = 31536000`

#### Scenario: Customized row remains unchanged

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 7200`
- **WHEN** the migration runs
- **THEN** the row still has `dashboard_session_ttl_seconds = 7200`
