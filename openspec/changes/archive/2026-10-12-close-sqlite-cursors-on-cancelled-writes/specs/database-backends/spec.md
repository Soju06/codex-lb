## ADDED Requirements

### Requirement: Cancelled SQLite writes release statement locks during connection teardown

File-backed SQLite main and background connections MUST release locks held by unfinished statements when connection teardown completes, even while cancelled tasks and their exception tracebacks remain referenced. Cancellation MUST continue to propagate and uncommitted writes MUST NOT become committed by teardown.

#### Scenario: Cancelled returning write retains its traceback

- **WHEN** a write returning rows is cancelled during execution and its connection teardown completes
- **AND** the cancelled task and traceback remain referenced
- **THEN** an independent connection can write without waiting for garbage collection
- **AND** the cancelled transaction remains uncommitted

#### Scenario: One cursor fails during connection cleanup

- **WHEN** closing one cursor raises an ordinary exception during connection teardown
- **THEN** cleanup MUST still attempt to close every remaining cursor and the connection
- **AND** the first cleanup exception MUST propagate after those attempts
- **AND** successfully finalized statements MUST NOT retain the writer lock

#### Scenario: Terminal transcript persistence times out

- **WHEN** a bridge terminal transcript write exceeds its persistence deadline and is cancelled
- **AND** the database worker finishes execution and connection teardown completes
- **THEN** subsequent bridge persistence and scheduler leader acquisition can write successfully
- **AND** the timed-out transcript remains subject to existing fenced settlement rules
