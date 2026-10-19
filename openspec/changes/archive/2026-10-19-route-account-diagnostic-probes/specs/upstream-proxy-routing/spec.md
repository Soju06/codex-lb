## ADDED Requirements

### Requirement: Diagnostic probes use the selected local account route

Force Probe and quota-planner warmup MUST resolve an upstream route using the selected local account ID before dispatching their model request. An active bound route MUST NOT fall back to direct egress, an environment proxy, or the default pool when route resolution or transport fails. Unbound accounts MUST retain the existing default/direct routing policy.

#### Scenario: Seats sharing a workspace retain separate routes
- **GIVEN** two local accounts share an upstream workspace ID and only one has an active proxy binding
- **AND** global proxy routing is disabled
- **WHEN** Force Probe or quota warmup runs for either account
- **THEN** the bound account MUST use its bound pool and the unbound account MUST use ordinary direct routing

#### Scenario: Unavailable binding prevents diagnostic dispatch
- **GIVEN** the selected account has an active binding to an unavailable pool or a pool without a usable endpoint
- **WHEN** a diagnostic probe is attempted
- **THEN** no model request MUST be dispatched
- **AND** Force Probe MUST return the dashboard upstream-proxy-unavailable error
- **AND** quota warmup MUST follow its existing failed-execution and reservation-release path

#### Scenario: Bound transport failure preserves diagnostic status
- **GIVEN** a bound Force Probe cannot connect through its resolved proxy
- **WHEN** the routed client reports a transport error
- **THEN** Force Probe MUST retain its network-failure status value of zero
- **AND** MUST NOT retry through a direct connection

#### Scenario: Header-only probe releases owned resources
- **WHEN** a routed Force Probe receives headers, fails, or is cancelled
- **THEN** its unbuffered response and owned client MUST be released
- **AND** cancellation during cleanup MUST be deferred until cleanup completes and then propagated

#### Scenario: Warmup logs describe route selection and failure
- **WHEN** quota warmup writes its existing success or failure request log
- **THEN** the log MUST include available route mode, pool, endpoint, and fallback metadata
- **AND** a route-resolution failure MUST include its credential-safe fail-closed reason
- **AND** a resolved endpoint on a failed connection MUST NOT be represented as proof of a completed connection
