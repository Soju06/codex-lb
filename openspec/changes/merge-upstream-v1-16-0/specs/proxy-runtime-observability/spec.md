## ADDED Requirements

### Requirement: Merge proxy runtime observability changes

The merged proxy runtime MUST expose upstream drain status, stream retry behavior, model fetch timeout handling, and request-log fields while preserving local observability for continuity owner resolution, bridge forwarding, rate limits, and fallback decisions.

#### Scenario: Drain status is visible during graceful deploys

- **WHEN** the application enters graceful drain or shutdown state
- **THEN** health or proxy status surfaces expose the merged drain status expected by upstream tests
- **AND** local readiness and continuity metrics remain available

#### Scenario: Transient stream retry is observable

- **WHEN** a transient upstream stream timeout is retried
- **THEN** the merged service records enough structured log or metric context to distinguish retry from permanent upstream failure
- **AND** request-log final state remains accurate
