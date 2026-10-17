## ADDED Requirements

### Requirement: Parent-bound subagent preference requires positive continuity evidence
The system MUST treat nonblank `x-openai-subagent` and `x-codex-parent-thread-id` metadata as child lineage only when an exact child thread identity is also available. `parent_bound_only` MUST activate only when durable routing state positively records that the exact parent thread used a nonblank `previous_response_id` with a resolved account owner. A shared process/session identifier, a parent-thread header by itself, or a soft parent locality mapping MUST NOT count as positive previous-response binding evidence. The durable evidence MUST contain only internal derived keys and account identity, not raw thread or response identifiers.

#### Scenario: Shared session is insufficient
- **GIVEN** a parent and child share a process session and the parent has only soft locality on account A
- **WHEN** `parent_bound_only` is enabled and the child starts
- **THEN** the system does not activate subagent diversification from that soft evidence

#### Scenario: Previous-response use establishes evidence
- **GIVEN** an exact parent thread sends a request with a nonblank `previous_response_id` whose owner resolves to account A
- **WHEN** the route is selected
- **THEN** the system records derived durable evidence that the exact parent thread is response-bound to account A
- **AND** it does not persist the raw parent thread id or response id in that evidence

