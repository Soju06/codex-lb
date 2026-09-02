## ADDED Requirements

### Requirement: Synthesized downstream turn state must remain provenance-scoped

A turn-state value synthesized by codex-lb for downstream reconnect and
internal affinity MUST remain available to those consumers without being
presented to the upstream server as a client-originated initial WebSocket
handshake header. A nonblank turn-state explicitly supplied by the client or
issued by upstream MAY continue through the existing owner-bound continuity
path.

#### Scenario: Initial WebSocket uses internal synthesized affinity only

- **GIVEN** a client opens a Responses WebSocket without `x-codex-turn-state`
- **WHEN** codex-lb accepts the downstream connection and opens upstream
- **THEN** the downstream accept MUST include a synthesized `x-codex-turn-state`
- **AND** internal continuity MUST be able to use that synthesized value
- **AND** the initial upstream handshake MUST NOT include that value as an `x-codex-turn-state` header.

#### Scenario: Explicit client turn state retains continuity semantics

- **GIVEN** a client reconnects with a nonblank `x-codex-turn-state`
- **WHEN** codex-lb opens the corresponding upstream connection
- **THEN** the value MUST retain its explicit client continuity provenance
- **AND** the synthesized-state omission rule MUST NOT silently reclassify it as LB-generated state.
