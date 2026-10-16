## ADDED Requirements

### Requirement: Full resends honor poisoned turn-state fallback anchors

When a generated HTTP turn state resolves to durable continuity and session
acquisition can fall back to a scoped thread or legacy session key, a complete
client resend MUST honor a known active anchor quarantine or abandonment tombstone
on that fallback key before proxy anchor injection and input trimming. The
request MUST retain its resolved account owner and the supplied full input.
Explicit client anchors and delta-only continuity MUST retain their existing
validation and fail-closed behavior. A sibling thread or different API-key
scope MUST NOT inherit the fallback key's fence.

#### Scenario: Split durable rows recover with full history

- **GIVEN** an older turn-state row retains an anchor and its prefix metadata
- **AND** its same-account thread fallback key carries a poison quarantine or abandonment tombstone
- **WHEN** a native client resends full history through either Responses HTTP route
- **THEN** the request dispatches on that owner without the proxy anchor
- **AND** its input is forwarded without prefix trimming
- **AND** a later continuation can complete

#### Scenario: A delta cannot bypass anchor poisoning

- **WHEN** the same split-row request contains only a new delta
- **THEN** the proxy retains its continuity checks and refuses the poisoned attach before upstream dispatch

#### Scenario: A newly discovered fence does not cause an endless resend loop

- **GIVEN** planning cached a circuit miss before a fallback fence was written
- **WHEN** submission discovers the fence and refuses its injected anchor
- **THEN** the next full-history retry honors the now-known fallback fence and can complete unanchored on its owner

#### Scenario: Client anchors remain explicit

- **WHEN** the client explicitly supplies a previous-response anchor
- **THEN** fallback quarantine handling does not silently remove that anchor

#### Scenario: Fences remain scoped

- **WHEN** the tombstone belongs to another thread or API-key scope
- **THEN** it does not suppress an otherwise valid anchored continuation
