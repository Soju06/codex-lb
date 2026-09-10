## ADDED Requirements

### Requirement: Post-terminal health failures preserve stream completion
After publishing a terminal Responses event, the proxy MUST log ordinary failures from subsequent account-error health writes without emitting another terminal event or aborting stream completion. This MUST apply to first-event, later-event, and raised upstream errors, with and without an API-key reservation. Required reservation settlement MUST precede the health write. Cancellation MUST retain its existing propagation and cleanup behavior.

#### Scenario: Account health persistence fails after a terminal response
- **WHEN** an upstream failure produces a terminal response and the subsequent account-error health write raises an ordinary exception
- **THEN** the client receives exactly one terminal event with the original response error and the stream completes normally
- **AND** the health-write failure is logged with exception information

#### Scenario: Keyed health persistence fails after ordered settlement
- **WHEN** a keyed continuation fails and its account-error health write raises after reservation settlement
- **THEN** settlement completes before the health write is attempted
- **AND** the client receives only the intended owner-unavailable terminal response
