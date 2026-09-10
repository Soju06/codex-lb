## ADDED Requirements

### Requirement: Native response interpretation preserves fork transport safeguards
Native response interpretation introduced by beta.6 MUST retain WebSocket byte-budget isolation, ordered accepted-message and terminal delivery under byte-budget overflow, and failure-phase diagnostics. A WebSocket MUST NOT fail solely because more than 64 events are queued within its byte budgets. Overflow and cancellation MUST release queued byte charges without affecting another connection's messages. HTTP transport queues MUST remain bounded.

#### Scenario: Interpreted WebSocket burst remains within its byte budget
- **GIVEN** a native WebSocket emits more than 64 response events within its byte budgets
- **WHEN** the response consumer drains them
- **THEN** interpretation metadata and event order are preserved without count-only overflow

#### Scenario: Native failure retains diagnostic and cleanup guarantees
- **WHEN** a native connection exceeds its byte budget after accepting messages
- **THEN** the consumer receives the accepted prefix followed by the failure with available phase diagnostics
- **AND** cleanup releases that connection's queued byte charges without cross-delivering events
