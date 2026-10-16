## ADDED Requirements

### Requirement: Native give-up closes the upstream stream and releases its account lease

When the native Responses path gives up on an upstream transport failure, the proxy MUST close the upstream stream generator that holds the account stream lease before it raises the `stream_incomplete` error. Each lease-holding wrapper between the consumer and the upstream stream MUST close its inner stream when it is closed or raises, so the upstream generator's cleanup runs and the account stream lease is released.

#### Scenario: Native transport give-up releases the stream lease

- **GIVEN** a native Responses stream whose upstream emitted at least one event and then a synthetic transport failure
- **WHEN** the proxy gives up and raises `stream_incomplete`
- **THEN** the upstream stream generator's cleanup runs
- **AND** the account stream lease is released
