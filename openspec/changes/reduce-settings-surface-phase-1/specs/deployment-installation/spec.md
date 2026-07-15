## ADDED Requirements

### Requirement: Removed configuration tunables are fixed constants and warn for one release

Values that are protocol constants or internal tuning details SHALL NOT be
operator-configurable. In particular, the OAuth protocol identity values
(authorization base URL, client id, originator, scope, redirect URI, and
callback port) MUST be fixed constants: they identify codex-lb to OpenAI
exactly like the Codex CLI, and changing any of them breaks login. When a
previously supported `CODEX_LB_*` setting is removed from the configuration
surface, its environment variable MUST be ignored without failing startup,
and for at least one release after removal, startup MUST emit a single
warning log listing every removed setting name found in the process
environment (never the values), referencing the simplicity principle that
motivated the removal. Incident-debugging trace logging SHALL be controlled
by the single `CODEX_LB_TRACE` comma-separated channel list, whose empty
default disables all trace channels.

#### Scenario: Removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_AUTH_BASE_URL` and `CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Clean environment starts without removal warnings

- **GIVEN** a deployment that sets no removed setting names
- **WHEN** the application starts
- **THEN** no removed-settings warning is logged

#### Scenario: Trace channels default to off

- **GIVEN** a default install with `CODEX_LB_TRACE` unset
- **WHEN** the proxy serves requests
- **THEN** no request-shape, payload, service-tier, or upstream trace logs
  are emitted

#### Scenario: A trace channel can be enabled for an incident

- **GIVEN** `CODEX_LB_TRACE=shape,upstream_payload`
- **WHEN** the proxy serves requests
- **THEN** request-shape and upstream-payload trace logs are emitted while
  all other trace channels stay off
