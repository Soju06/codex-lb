## MODIFIED Requirements

### Requirement: OAuth routing distinguishes targeted accounts from bootstrap flows

Targeted OAuth operations MUST resolve the intended local account's current route: its active binding, or normal default/direct policy if unbound. Unknown targets and unusable active bindings MUST fail before upstream I/O. Untargeted OAuth MUST retain bootstrap policy: when active account bindings exist, use the default pool or fail closed if unavailable.

#### Scenario: Targeted bound account needs no default pool
- **GIVEN** the intended local account has an active bound proxy pool and no global default exists
- **WHEN** browser callback exchange, manual callback exchange, device-code initialization, or device-token polling is performed
- **THEN** the operation MUST use the intended account's bound route
- **AND** direct egress MUST be disabled for that operation

#### Scenario: Targeted unbound account retains direct routing
- **GIVEN** a targeted local account has no binding and global proxy routing is disabled
- **AND** another local account has an active binding
- **WHEN** the targeted account reauthenticates
- **THEN** its server-side OAuth operations MAY use the existing direct route
- **AND** MUST NOT use the other account's proxy

#### Scenario: Bound route failure prevents token exchange
- **GIVEN** the targeted account's bound pool is inactive or has no active endpoint
- **WHEN** a server-side OAuth operation is attempted
- **THEN** no token exchange or device request MUST be dispatched

#### Scenario: Unknown target fails before network I/O
- **GIVEN** the intended local account does not exist
- **WHEN** a server-side OAuth operation is attempted
- **THEN** it MUST fail before upstream I/O

#### Scenario: OAuth fails closed when bindings exist but no default pool is configured
- **GIVEN** an OAuth flow has no intended local account and active account bindings exist
- **AND** no default pool is configured
- **WHEN** a server-side OAuth operation is attempted
- **THEN** it MUST fail before upstream I/O
- **AND** the error MUST indicate that no upstream proxy route is available

#### Scenario: OAuth uses default pool when bindings exist and pool is configured
- **GIVEN** an OAuth flow has no intended local account and active account bindings exist
- **AND** a default pool is configured with an active endpoint
- **WHEN** a server-side OAuth operation is attempted
- **THEN** the request MUST go through the default pool's endpoint

#### Scenario: OAuth preserves direct egress when no proxy bindings exist
- **GIVEN** an OAuth flow has no intended local account
- **AND** no active account proxy bindings exist and global proxy routing is disabled
- **WHEN** a server-side OAuth operation is attempted
- **THEN** the request MAY use direct egress or an environment proxy as before

## RENAMED Requirements

- FROM: `### Requirement: OAuth token exchange must use a proxy pool when active proxy bindings exist`
- TO: `### Requirement: OAuth routing distinguishes targeted accounts from bootstrap flows`
