## MODIFIED Requirements

### Requirement: Dashboard password sessions use a configurable absolute lifetime

The system SHALL issue dashboard password-authenticated sessions with an absolute lifetime controlled by persisted dashboard settings. The default persisted lifetime SHALL be 1 year. Configured lifetimes at or below 30 days SHALL apply to newly issued dashboard password sessions by setting both the encrypted session expiry payload and the cookie `Max-Age` to the same value. Configured lifetimes above 30 days SHALL apply only in standard dashboard auth mode when the request is socket-level local, or when an explicit loopback-host-header override is enabled, the request uses a loopback dashboard URL, and every field value of every forwarded client-IP header is empty. Non-loopback, proxy-aware, trusted-header, or bridge-without-override requests MUST receive a 12-hour effective lifetime without rewriting the persisted setting.

#### Scenario: Newly issued dashboard password session honors configured lifetime

- **WHEN** an admin configures a dashboard session lifetime and successfully completes password authentication from a socket-level local request
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime
- **AND** the cookie `Max-Age` matches the same configured lifetime

#### Scenario: Long localhost-published bridge session requires explicit override

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication through a loopback dashboard URL whose socket peer is not loopback
- **AND** the explicit loopback-host-header override is disabled
- **THEN** the newly issued dashboard session expires after 12 hours

#### Scenario: Long localhost-published bridge session can opt in

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication through a loopback dashboard URL whose socket peer is not loopback
- **AND** the explicit loopback-host-header override is enabled
- **AND** every field value of every forwarded client-IP header is empty
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime

#### Scenario: Later duplicate forwarded client identity disables the long session override

- **WHEN** a non-loopback socket peer authenticates through a loopback dashboard URL with the explicit loopback-host-header override enabled
- **AND** a forwarded client-IP header contains an empty first field followed by a non-empty field
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Long dashboard password session falls back for non-loopback access

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication from a non-loopback, proxy-aware, or trusted-header request
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Existing dashboard sessions keep their original expiry

- **WHEN** an admin changes the configured dashboard session lifetime after a session cookie has already been issued
- **THEN** previously issued cookies continue to expire according to the expiry embedded in their encrypted payload
- **AND** only newly issued dashboard password sessions use the updated lifetime
