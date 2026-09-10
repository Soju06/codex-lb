## MODIFIED Requirements

### Requirement: Compact request-path latency is bounded without cutting off valid long compactions

`/responses/compact` MUST use the same default bounded request budget as an
ordinary Responses stream. Its per-request budget MUST bound account
selection, freshness, connection, and upstream response handling. If an
operator configures a smaller `compact_request_budget_seconds`, that value
MUST cap the upstream compact call without extending the per-request budget.
The compact SSE idle deadline MUST remain independently controlled by the
explicit compact timeout override when present, otherwise by
`stream_idle_timeout_seconds`; the idle deadline MUST NOT be replaced by the
overall compact request budget.

#### Scenario: Slow valid compact remains within the standard Responses window

- **GIVEN** the default `compact_request_budget_seconds`
- **WHEN** `/responses/compact` starts its upstream call
- **THEN** the call receives the remaining default Responses-stream budget
- **AND** the proxy does not impose the former 150-second upstream cap

#### Scenario: Explicit compact budget remains effective

- **GIVEN** an operator configures a smaller `compact_request_budget_seconds`
- **WHEN** `/responses/compact` starts its upstream call
- **THEN** the upstream call timeout does not exceed the configured compact budget
- **AND** the remaining request budget can shorten that timeout further
- **AND** cancellation and timeout settlement remain unchanged

#### Scenario: Compact idle and total deadlines remain independent

- **GIVEN** a compact request has an explicit remaining total-timeout override
- **WHEN** `/responses/compact` starts an SSE upstream call
- **THEN** the overall request deadline uses the remaining total-timeout override
- **AND** the SSE idle deadline uses that explicit compact override, or the
  configured generic stream idle timeout when no override is present
