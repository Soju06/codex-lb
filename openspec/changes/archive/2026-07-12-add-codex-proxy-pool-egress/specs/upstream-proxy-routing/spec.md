## ADDED Requirements

### Requirement: Account-bound upstream traffic must use the bound proxy pool
When an account has an explicit upstream proxy pool binding, every ChatGPT/OpenAI/Codex upstream operation using that account's credentials MUST resolve a route from the bound pool before opening a network connection.

#### Scenario: Bound pool unavailable fails closed
- **GIVEN** an account has an explicit upstream proxy pool binding
- **AND** the bound pool has no active usable endpoint
- **WHEN** an account-scoped ChatGPT upstream operation is attempted
- **THEN** the operation MUST fail before opening an upstream network connection
- **AND** it MUST NOT use the default pool, environment proxy, or direct egress.

#### Scenario: Warmup and compact operations obey account-bound routing
- **GIVEN** an account has an explicit upstream proxy pool binding
- **WHEN** the system performs warmup or compact Responses operations with that account's credentials
- **THEN** the operation MUST resolve and use a route from the bound pool before opening the upstream connection
- **AND** it MUST fail closed instead of falling back to direct egress when no bound route is available.

#### Scenario: Auth import does not perform direct usage refresh when proxy routing is required
- **GIVEN** upstream proxy routing is enabled
- **AND** an imported account has no usable account-bound or default proxy route
- **WHEN** an operator imports that account from `auth.json`
- **THEN** the import MUST save the account as paused before any usage-refresh network request is opened
- **AND** it MUST NOT perform the import-time usage refresh through direct egress.

#### Scenario: Proxy binding releases import-paused account
- **GIVEN** an account was paused because proxy routing was required during `auth.json` import
- **WHEN** an operator saves an active upstream proxy binding for that account
- **THEN** the account SHALL be reactivated so it can enter the routed account pool.

### Requirement: Codex upstream Codex client must require a resolved route and built-in TLS fingerprint
Affected Codex upstream HTTP and websocket calls MUST use the Codex upstream client with an explicit resolved route and the built-in Codex CLI TLS fingerprint.

#### Scenario: Runtime fingerprint override rejected
- **WHEN** a caller attempts to pass runtime fingerprint kwargs such as `impersonate`, `ja3`, `akamai`, or `extra_fp`
- **THEN** the client MUST reject the call before opening a network connection.

### Requirement: Route metadata must be persisted for migrated upstream calls
Request logs for migrated upstream calls MUST record route mode, proxy pool id, proxy endpoint id, same-pool fallback use, and fail-closed reason where applicable.

#### Scenario: Fail-closed reason recorded
- **GIVEN** route resolution fails closed before network open
- **WHEN** the request log is written
- **THEN** the log MUST include the fail-closed reason without proxy credentials.

### Requirement: Codex installation metadata must be account-owned
Codex `response.create` requests sent through account-scoped bridge or websocket transports MUST use the selected local account's stored `x-codex-installation-id` value in `client_metadata`.

#### Scenario: Client-supplied installation id is replaced
- **GIVEN** a client sends `client_metadata.x-codex-installation-id`
- **AND** codex-lb selects account `A`
- **WHEN** codex-lb sends the upstream `response.create` request
- **THEN** the upstream `client_metadata.x-codex-installation-id` MUST equal account `A`'s stored installation id
- **AND** it MUST NOT equal the client-supplied value.

### Requirement: Upstream proxy pool membership must reject duplicates
Dashboard upstream proxy pool member mutations MUST reject attempts to add an endpoint that is already a member of the target pool with a validation error instead of surfacing a database integrity failure.

#### Scenario: Duplicate pool member rejected
- **GIVEN** a proxy pool already contains endpoint `E`
- **WHEN** an admin adds endpoint `E` to the same pool again
- **THEN** the API MUST return a dashboard validation error
- **AND** it MUST NOT return an unhandled server error.
