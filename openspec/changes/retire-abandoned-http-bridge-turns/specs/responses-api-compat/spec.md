## MODIFIED Requirements

### Requirement: HTTP bridge streaming requests release session admission on downstream disconnect

When a downstream client disconnects from a streaming Responses request that is
using the HTTP responses bridge, the service MUST promptly route cancellation
through the existing bridge request cleanup path. The cleanup MUST release the
per-session response-create admission state, cancel request reservation
heartbeats, settle or release API-key reservations using the existing settlement
order, retire or reconnect the affected upstream bridge turn, and release the
account stream lease. The service MUST NOT replay a request that has already
emitted downstream-visible output.

#### Scenario: Post-visible downstream disconnect retires the bridge turn

- **GIVEN** a bridge-routed streaming Responses request has emitted
  downstream-visible output and has not reached a terminal upstream event
- **WHEN** the downstream client disconnects or aborts the stream
- **THEN** the service closes the orphaned upstream bridge turn through the
  existing detach/finalization path
- **AND** a later request for the same bridge session key can acquire admission
  within the configured admission wait window
- **AND** the abandoned post-visible request is not replayed locally

#### Scenario: Downstream disconnect watcher is bounded

- **GIVEN** the service starts any helper task to observe downstream disconnects
- **WHEN** the bridge stream completes, fails, or is torn down
- **THEN** the helper task MUST be cancelled or awaited
- **AND** the helper task MUST NOT release bridge admission through a second
  independent path
