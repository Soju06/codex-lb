## ADDED Requirements

### Requirement: Bridge-enabled worker pools use addressable bridge owners

When the HTTP responses session bridge is enabled and the configured runtime worker count is greater than one, the service MUST NOT start a plain Uvicorn multi-worker process group with a shared bridge instance id. It MUST instead start a front listener plus single-worker backend processes where each backend has a unique bridge instance id and an advertised endpoint that can route owner handoff to that worker-local bridge session map.

#### Scenario: bridge-enabled multi-worker startup uses addressable workers

- **WHEN** the HTTP responses session bridge is enabled
- **AND** the configured worker count is greater than one
- **THEN** startup launches single-worker backend app processes with unique bridge instance ids
- **AND** each backend advertises a worker-specific bridge endpoint
- **AND** public HTTP and WebSocket traffic enters through one front listener

#### Scenario: bridge-disabled multi-worker startup remains plain Uvicorn

- **WHEN** the HTTP responses session bridge is disabled
- **AND** the configured worker count is greater than one
- **THEN** startup continues to pass the requested worker count directly to Uvicorn
