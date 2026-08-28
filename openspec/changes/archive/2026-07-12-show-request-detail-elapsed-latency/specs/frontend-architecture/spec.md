## ADDED Requirements

### Requirement: Request detail dialog displays elapsed latency
The dashboard request-log `View Details` dialog SHALL display `latency_ms` as an `Elapsed` field next to the `Plan` field. The display value SHALL use `ms` units for values under 1000 ms and `s` units (to one decimal) for values 1000 ms or greater. When `latency_ms` is null, the field SHALL render an em dash (`—`).

#### Scenario: Latency under one second shown in ms
- **WHEN** a request log detail dialog opens and the row has `latency_ms: 500`
- **THEN** the dialog displays `500 ms` in the `Elapsed` field

#### Scenario: Latency at or above one second shown in seconds
- **WHEN** a request log detail dialog opens and the row has `latency_ms: 1500`
- **THEN** the dialog displays `1.5 s` in the `Elapsed` field

#### Scenario: Missing latency renders em dash
- **WHEN** a request log detail dialog opens and the row has `latency_ms: null`
- **THEN** the dialog displays `—` in the `Elapsed` field
