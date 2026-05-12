## ADDED Requirements

### Requirement: Sticky routing remains deterministic after upstream routing merge

The upstream routing and load-balancer changes MUST be merged without breaking local sticky-session, continuity owner handoff, file-id affinity, or account takeover behavior.

#### Scenario: File-id affinity selects the registering account

- **GIVEN** a file was registered through the merged files protocol
- **WHEN** a later request references that file
- **THEN** sticky routing selects the account associated with the file when eligible
- **AND** it falls back only according to the merged proxy failure policy

#### Scenario: Existing continuity sessions keep owner semantics

- **GIVEN** a continuity session has an owner or durable bridge record
- **WHEN** a merged request is routed during owner handoff, reconnect, or soft drain
- **THEN** the selected account and owner forwarding behavior remain deterministic
- **AND** stale alias or same-account takeover races are covered by targeted tests
