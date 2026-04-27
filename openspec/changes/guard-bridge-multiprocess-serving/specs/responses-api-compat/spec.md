### Requirement: HTTP bridge serving is single-worker unless bridge ownership is worker-addressable

When the HTTP responses session bridge is enabled, application startup MUST refuse Uvicorn worker counts greater than one unless bridge ownership and forwarding can target the worker-local session owner. The service MUST fail closed before serving traffic rather than allowing multiple worker processes to share one bridge instance id with separate in-memory session maps.

#### Scenario: bridge-enabled multi-worker startup is rejected

- **WHEN** the HTTP responses session bridge is enabled
- **AND** the configured Uvicorn worker count is greater than one
- **THEN** startup fails with an operator-facing error that instructs the operator to use one worker or disable the bridge

#### Scenario: bridge-disabled multi-worker startup is allowed

- **WHEN** the HTTP responses session bridge is disabled
- **AND** the configured Uvicorn worker count is greater than one
- **THEN** startup continues and passes the requested worker count to Uvicorn
