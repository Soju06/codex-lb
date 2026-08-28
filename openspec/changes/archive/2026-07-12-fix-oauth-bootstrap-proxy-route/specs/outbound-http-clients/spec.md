## ADDED Requirements

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
