## ADDED Requirements

### Requirement: SQLite write-lock stalls are attributable

When a SQLite write transaction holds the writer slot longer than the configured busy timeout, the system MUST report it at WARNING when the transaction ends, including the held duration, whether it committed or rolled back, the owning task where available, and the first and last write statements it executed. The window MUST be measured from the transaction's first write statement, so read-only transactions — which never take the writer slot in WAL — are never reported. The watchdog MUST NOT raise into the query path and MUST NOT require configuration.

#### Scenario: The starving writer is identified when it finally ends

- **GIVEN** a write transaction that held the writer slot past the busy timeout while other writers surfaced `database is locked`
- **WHEN** it commits or rolls back
- **THEN** a warning reports its duration, outcome, task, and first/last write statements

#### Scenario: Healthy traffic is silent

- **WHEN** read-only transactions and writes completing under the threshold run
- **THEN** no long-write report is produced
