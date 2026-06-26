## ADDED Requirements

### Requirement: Startup probe timeouts do not emit shielded-future diagnostics

When the streaming proxy's startup probe times out waiting for the first upstream
event and the probed task later fails with an upstream error, the system SHALL
deliver that error through the streamed response without emitting an
`exception in shielded future` or `exception was never retrieved` diagnostic to
the asyncio loop exception handler.

#### Scenario: Timed-out probe whose upstream later returns 429

- **GIVEN** the startup probe times out before the first upstream event arrives
- **WHEN** the probed task subsequently fails with a 429 from the admission gate
- **THEN** the upstream error is surfaced to the caller through the streamed response
- **AND** no `exception in shielded future` diagnostic is logged

#### Scenario: Probe stream dropped before the first item is consumed

- **GIVEN** the startup probe times out and hands the running task to the response
- **WHEN** the wrapping stream is dropped before the task is awaited
- **THEN** the probed task's failure does not log an `exception was never retrieved` warning
