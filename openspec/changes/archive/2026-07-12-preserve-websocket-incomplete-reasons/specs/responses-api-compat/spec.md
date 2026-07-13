## ADDED Requirements

### Requirement: WebSocket incomplete responses preserve the upstream reason in request logs

When an upstream Responses WebSocket terminal `response.incomplete` event contains a non-empty string at `response.incomplete_details.reason`, the service SHALL persist the request log with status `error` and SHALL preserve that reason as both `error_code` and `error_message`. The terminal event sent to the downstream client and the account-health treatment of an incomplete response SHALL remain unchanged.

#### Scenario: max-output limit is identifiable in a WebSocket request log

- **WHEN** the upstream emits `response.incomplete` with
  `incomplete_details.reason` equal to `max_output_tokens`
- **THEN** the corresponding WebSocket request log has status `error`,
  `error_code` equal to `max_output_tokens`, and `error_message` equal to
  `max_output_tokens`
- **AND** the account is not marked unhealthy solely because of that
  incomplete event
