## ADDED Requirements

### Requirement: Direct HTTP stream continuity conflicts surface the conflict code

When a direct HTTP (SSE) stream fails closed because required continuity-owner
selection reported `continuity_owner_conflict`, the emitted `response.failed`
error envelope MUST carry the `continuity_owner_conflict` error code and the
selection's conflict message rather than the generic
`previous_response_owner_unavailable` code. The continuity fail-closed
telemetry for that failure MUST record surface `http_stream` with reason
`owner_conflict` and MUST propagate the selection error code as the upstream
error code, and the persisted request log MUST record the surfaced conflict
code. A preferred-owner selection failure without a conflict code MUST keep
the existing `previous_response_owner_unavailable` envelope, the
`owner_account_unavailable` telemetry reason, and the existing upstream error
codes.

#### Scenario: Conflicting continuity owners on a direct stream

- **GIVEN** a direct HTTP stream request whose required continuity-owner selection fails with `continuity_owner_conflict`
- **WHEN** the stream fails closed without a selected account
- **THEN** the SSE `response.failed` event carries error code `continuity_owner_conflict` and the selection's conflict message
- **AND** continuity fail-closed telemetry records surface `http_stream`, reason `owner_conflict`, and upstream error code `continuity_owner_conflict`
- **AND** the persisted request log records error code `continuity_owner_conflict`

#### Scenario: Owner unavailability without a conflict is unchanged

- **GIVEN** a direct HTTP stream request whose preferred continuity owner cannot be selected
- **AND** selection did not report `continuity_owner_conflict`
- **WHEN** the stream fails closed without a selected account
- **THEN** the SSE `response.failed` event carries `previous_response_owner_unavailable`
- **AND** continuity fail-closed telemetry records reason `owner_account_unavailable` with the existing upstream error codes
