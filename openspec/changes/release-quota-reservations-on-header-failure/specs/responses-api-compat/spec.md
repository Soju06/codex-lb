## ADDED Requirements

### Requirement: HTTP bridge session cleanup has exact-once ownership

Each HTTP responses bridge session that leaves local reuse MUST retain exactly one owner responsible for bounded upstream close and resource release. This applies to every direct, scheduled, reader-driven, and shutdown cleanup entry point, including but not limited to failed registration, account-binding invalidation, registry or capacity eviction, upstream-reader retirement, local terminal reset, and process shutdown. Competing cleanup paths MUST share a session-level ownership claim and MUST NOT close or release the same session more than once. A path that removes or detaches a session from the local registry MUST establish or preserve that close owner before its first post-detachment await. Owned close work MUST remain tracked when bounded cleanup times out or the initiating caller is cancelled, so shutdown can drain the work instead of abandoning it. Local terminal reset MUST run its owned close fallback even when pending-request cleanup fails or is cancelled. Shutdown MUST claim all unowned registered sessions before its first post-detachment await, process every claimed session despite an individual close failure, drain tracked bridge-close work, and defer caller cancellation until that cleanup completes.

#### Scenario: Competing reader and direct cleanup close once

- **GIVEN** an HTTP bridge session is being retired by its upstream reader
- **WHEN** any direct, scheduled, or shutdown cleanup path concurrently
  attempts to close the same session
- **THEN** exactly one path owns and initiates bounded close
- **AND** durable ownership, the account lease, and upstream resources are not
  released a second time by a losing path

#### Scenario: Detached reader retirement retains cleanup ownership

- **GIVEN** an HTTP bridge session has already been detached from local reuse
- **WHEN** its upstream reader finishes retiring the session
- **THEN** the session still receives one bounded close unless another path
  already owns that close
- **AND** the close remains tracked until completion or shutdown drain

#### Scenario: Terminal-reset interruption still closes the session

- **GIVEN** local terminal reset has removed an HTTP bridge session from the
  registry and claimed its close
- **WHEN** pending-request cleanup fails or the reset caller is cancelled
- **THEN** bounded session close still runs exactly once
- **AND** the pending-cleanup failure or caller cancellation propagates only
  after the owned close completes or is transferred to tracked cleanup

#### Scenario: Shutdown interruption drains every owned session

- **GIVEN** shutdown has detached multiple registered HTTP bridge sessions
- **WHEN** one session close fails or the shutdown caller is cancelled while
  cleanup is in progress
- **THEN** every session claimed by shutdown is still processed exactly once
- **AND** already reader-owned close work is included in the shutdown drain
- **AND** tracked bridge-close work is drained before caller cancellation is
  propagated
