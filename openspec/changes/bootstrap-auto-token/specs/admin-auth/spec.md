## ADDED Requirements

### Requirement: Auto-generated bootstrap token

The system SHALL auto-generate a cryptographically random bootstrap token on startup when ALL of the following conditions are true: (1) no dashboard password is configured (`password_hash` is NULL), (2) no manual `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var is set. The token MUST be generated using `secrets.token_urlsafe(32)` (256 bits entropy). The system MUST store only a SHA-256 hash of the token in the shared `dashboard_settings` row so every replica can validate the same token without persisting the reusable secret at rest.

#### Scenario: Auto-generation on fresh install without env var

- **WHEN** the server starts with no configured password and no `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var
- **THEN** the system generates a random token, stores only its hash in shared database-backed state, and logs the plaintext token once

#### Scenario: Auto-generation skipped when env var is set

- **WHEN** the server starts with `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` configured
- **THEN** the system does not auto-generate a token and uses the env var value

#### Scenario: Auto-generation skipped when password exists

- **WHEN** the server starts with a dashboard password already configured
- **THEN** the system does not auto-generate a token

### Requirement: Bootstrap token log output

The system SHALL print the auto-generated bootstrap token to server logs on startup using `logger.info()` with visual delimiters for easy identification in `docker logs` output. The log message MUST include the token value and a brief instruction to use it for initial remote setup.

#### Scenario: Token printed to logs on first run

- **WHEN** an auto-generated token is created during startup
- **THEN** the server logs contain the token in a visually distinct format

#### Scenario: No token logged when env var is set

- **WHEN** `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` is configured
- **THEN** no auto-generated token appears in the logs

### Requirement: Bootstrap token priority chain

The system SHALL resolve bootstrap validation using the following priority: (1) manual `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var, (2) shared auto-generated token hash from `dashboard_settings`, (3) None. Dedicated helpers MUST expose (a) whether a bootstrap token is configured and (b) whether a submitted token matches the configured value.

#### Scenario: Env var takes priority over auto-generated token

- **WHEN** both an env var and shared auto-generated token exist
- **THEN** `get_active_bootstrap_token()` returns the env var value

#### Scenario: Auto-generated token used when no env var

- **WHEN** no env var is set and a shared token hash exists
- **THEN** the system accepts only submitted tokens whose hash matches the stored shared hash

#### Scenario: None returned when neither exists

- **WHEN** no env var is set and no auto-generated token exists
- **THEN** `get_active_bootstrap_token()` returns None

### Requirement: Bootstrap token cleared after password setup

The system SHALL clear the shared auto-generated bootstrap token immediately after a successful password setup via `POST /api/dashboard-auth/password/setup`. The manual env var token is unaffected (it remains readable from the environment).

#### Scenario: Auto-generated token cleared after password set

- **WHEN** a password is successfully configured using the auto-generated token
- **THEN** the shared stored auto-generated token is cleared
- **AND** subsequent calls to `get_active_bootstrap_token()` return None (unless env var is set)

### Requirement: Bootstrap token works across replicas

The system SHALL make the same auto-generated bootstrap token valid across all replicas and restarts until password setup succeeds.

#### Scenario: Token generated on one replica is accepted by another

- **WHEN** replica A generates the auto bootstrap token and the password is still unset
- **AND** the user submits that token to replica B behind a load balancer
- **THEN** replica B accepts the same token for `POST /api/dashboard-auth/password/setup`

### Requirement: Existing bootstrap tokens are not re-logged on restart

The system SHALL log an auto-generated bootstrap token only when it creates or explicitly regenerates a new token, not on ordinary restarts that reuse an existing stored hash.

#### Scenario: Restart does not republish stored bootstrap token

- **WHEN** a shared auto-generated bootstrap token hash already exists and no password is configured
- **THEN** startup does not log the old plaintext token again

### Requirement: Password removal regenerates bootstrap access

The system SHALL immediately recreate an auto-generated bootstrap token after a successful dashboard password removal when no manual bootstrap token env var is configured.

#### Scenario: Removing password recreates bootstrap token without restart

- **WHEN** an authenticated admin removes the dashboard password
- **AND** no manual `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var is configured
- **THEN** a new auto-generated bootstrap token is stored and logged immediately
- **AND** a subsequent remote `GET /api/dashboard-auth/session` reports `{ "bootstrapTokenConfigured": true }`

## MODIFIED Requirements

### Requirement: Session state endpoint

The system SHALL expose `GET /api/dashboard-auth/session` returning the current authentication state including `password_required` (whether a password is configured), `authenticated` (whether the session is fully valid), `totp_required_on_login`, `totp_configured`, and bootstrap flags used for first-run remote setup. The `bootstrap_token_configured` field MUST reflect the result of `get_active_bootstrap_token()` (which includes auto-generated tokens), not just the env var.

#### Scenario: No password configured

- **WHEN** `password_hash` is NULL
- **THEN** the response contains `{ "passwordRequired": false, "authenticated": true, "totpRequiredOnLogin": false, "totpConfigured": false }`

#### Scenario: Password set, not logged in

- **WHEN** `password_hash` is set and no valid session cookie exists
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, ... }`

#### Scenario: Logged in, TOTP pending

- **WHEN** session has `pw=true, tv=false` and `totp_required_on_login` is true
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, "totpRequiredOnLogin": true, "totpConfigured": true }`

#### Scenario: Remote bootstrap required before first password setup

- **WHEN** `password_hash` is NULL, `totp_required_on_login` is false, and the session request comes from a non-local client
- **THEN** the response contains `{ "passwordRequired": false, "authenticated": false, "bootstrapRequired": true }`

#### Scenario: Bootstrap token always configured on fresh install

- **WHEN** `password_hash` is NULL and an auto-generated token exists (no env var set)
- **THEN** the session response contains `{ "bootstrapTokenConfigured": true }`
