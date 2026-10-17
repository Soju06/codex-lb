## ADDED Requirements

### Requirement: Idle upstream loss invalidates the Codex downstream transport
When an ordinary Codex WebSocket upstream ends after completed work and there are no pending or replay-owned requests, the proxy MUST close the downstream with reconnect code 1012 and retire only the continuity anchor belonging to that physical upstream. It MUST NOT silently attach a later delta to a new physical upstream. Durable account ownership MUST remain unchanged. Active request termination and public OpenAI-compatible WebSocket behavior MUST retain their existing contracts.

#### Scenario: Completed upstream disappears
- **WHEN** an upstream closes or fails after a completed Codex response with no pending request
- **THEN** the downstream receives close code 1012 before a subsequent turn
- **AND** a full-context reconnect does not receive an injected anchor from the lost socket

#### Scenario: Preparation overlaps the idle close
- **WHEN** a new turn is received but request preparation or admission has not completed when the idle close commits
- **THEN** that turn MUST NOT be dispatched upstream after the downstream reconnect notification
- **AND** its reservation and admission ownership MUST be finalized

#### Scenario: Another connection owns the newer anchor
- **WHEN** an idle upstream ends after another connection has established the shared continuity anchor
- **THEN** the newer connection's anchor remains intact

#### Scenario: Active work or public route
- **WHEN** an upstream ends while work is pending, replay-owned, or on the public OpenAI-compatible route
- **THEN** its existing terminal and replay behavior is preserved
