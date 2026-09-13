## ADDED Requirements

### Requirement: Daybreak capability intent fails closed before warmup fan-out

`POST /v1/warmup` and `POST /v1/warmup/{mode}` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before mode validation, account-pool evaluation, or any upstream warmup submission. Headerless warmup requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before warmup routing

- **WHEN** a valid proxy API key sends either warmup route with the Daybreak carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no account pool is evaluated and no upstream warmup is submitted

#### Scenario: Headerless warmup behavior remains unchanged

- **WHEN** a warmup request omits the required-capability carrier
- **THEN** the existing authentication, mode, account-scope, and fan-out behavior remains in effect
