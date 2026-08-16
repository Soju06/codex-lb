## ADDED Requirements

### Requirement: Process shutdown establishes a pre-connection drain barrier

The project-owned Uvicorn server MUST commit graceful drain before Uvicorn closes HTTP or WebSocket connections. A deadline-bearing preStop request or the first shutdown transition MUST establish one monotonic application drain deadline; later SIGTERM, shutdown, or lifespan transitions MUST reuse that deadline without extending it. A delayed preStop request MAY tighten a signal-committed deadline only to preserve the earlier absolute deadline carried by that request. A headerless operator drain MUST remain reversible. Once process shutdown is committed, operator actions MUST NOT reopen admission or erase its deadline.

#### Scenario: Direct SIGTERM precedes connection shutdown

- **WHEN** the process receives SIGTERM with an admitted Responses turn active and no prior preStop request
- **THEN** drain admission closes before Uvicorn invokes connection shutdown
- **AND** Uvicorn waits for the turn within the remaining application deadline

#### Scenario: preStop is followed by SIGTERM

- **WHEN** preStop starts drain and SIGTERM arrives later
- **THEN** SIGTERM reuses the original absolute deadline
- **AND** local preStop request latency has already consumed that deadline
- **AND** it does not start a second drain period

#### Scenario: SIGTERM overtakes a deadline-bearing preStop request

- **WHEN** preStop anchors an absolute deadline and SIGTERM commits a later deadline before the local start request is handled
- **THEN** the accepted preStop deadline tightens the committed process deadline
- **AND** every later shutdown stage uses that earlier absolute value
- **AND** an in-flight drain wait that already started re-reads and adopts that earlier deadline

#### Scenario: SIGTERM interleaves with deadline initialization

- **WHEN** a drain-start invocation observes no prior drain and a synchronous shutdown signal commits between its later state operations
- **THEN** both invocations publish monotonic deadline candidates
- **AND** every drain stage uses the earlier candidate
- **AND** neither stale continuation can extend the effective deadline

#### Scenario: Drain stop races committed shutdown

- **WHEN** an operator drain stop interleaves with process shutdown commitment
- **THEN** committed WebSocket and HTTP admission remains closed
- **AND** the committed deadline remains available to every later drain stage

#### Scenario: Signal commit precedes matching lifespan startup

- **WHEN** a server start prepares shutdown state and SIGTERM commits the barrier before its application lifespan begins
- **THEN** matching lifespan startup preserves the committed barrier and deadline
- **AND** it does not reset process shutdown state

#### Scenario: Completed embedded lifespan is followed by a new start

- **WHEN** an embedded application lifespan has completed and another embedded lifespan starts in the same process
- **THEN** the new lifecycle starts with admission open and no inherited committed deadline

### Requirement: Graceful drain closes WebSocket admission

Once graceful drain begins, the application MUST reject every new external WebSocket connection before invoking the route handler. A Responses WebSocket scope admitted before the barrier MUST remain tracked until its handler exits. Other WebSocket protocols MUST receive the same late-admission rejection but MUST NOT hold the Responses in-flight counter for their full connection lifetime.

#### Scenario: New WebSocket arrives during drain

- **WHEN** a new WebSocket connection scope arrives after drain has begun
- **THEN** the application rejects the connection without invoking its route handler
- **AND** the rejected connection does not increase the in-flight count

#### Scenario: WebSocket crosses the drain barrier

- **WHEN** a Responses WebSocket scope is admitted immediately before drain begins
- **THEN** it remains in the in-flight count until its route handler exits
- **AND** shutdown waits for that scope within the configured drain timeout

#### Scenario: Realtime or Live connection predates drain

- **WHEN** a non-Responses WebSocket scope is admitted before drain
- **THEN** it does not hold the Responses in-flight counter
- **AND** normal Uvicorn connection shutdown remains its lifecycle bound

### Requirement: Graceful drain preserves active WebSocket turn finalization

An admitted Responses WebSocket connection MUST stop accepting new `response.create` turns after drain begins. An idle connection MUST close promptly, while a connection with an already registered active turn MUST remain open until that turn reaches terminal downstream delivery, request-log persistence ownership, and API-key settlement ownership or the shared drain deadline expires.

#### Scenario: Idle admitted WebSocket observes drain

- **WHEN** drain begins while an admitted Responses WebSocket has no active turn
- **THEN** the server closes that connection promptly
- **AND** the connection no longer holds the shutdown drain

#### Scenario: Active turn completes during drain

- **WHEN** drain begins while an admitted Responses WebSocket turn is active
- **THEN** the turn continues through terminal downstream delivery, request logging, and API-key settlement
- **AND** the WebSocket closes after the active turn is finalized

#### Scenario: Existing connection submits a new turn during drain

- **WHEN** an admitted Responses WebSocket submits a new `response.create` after drain begins
- **THEN** the server rejects that turn locally
- **AND** the turn is not registered or sent upstream

#### Scenario: Upstream clean close races a new turn

- **WHEN** an upstream transport-end frame is received while a new turn is blocked in admission or account ownership
- **THEN** the connection is synchronously marked for reconnect before that attribution wait
- **AND** the reader does not fail or replay-mutate the still-unsent turn
- **AND** the sender checks the latch after all admission and account awaits immediately before send
- **AND** the turn is sent exactly once on a fresh upstream socket
- **AND** the retired account-local create lease is released and the fresh account re-acquires its own lease
- **AND** no post-send replay budget is consumed

#### Scenario: Generic send failure races a reader-owned clean-close replay

- **WHEN** a generic upstream send failure occurs after the reader has classified the same sent turn as clean-close replayable
- **THEN** the sender marks the connection for reconnect before cancelling and awaiting the reader
- **AND** harvests the reader's published replay owner before retiring its control state
- **AND** releases the retired account-local create lease before a replacement account acquires its own lease
- **AND** sends the turn exactly once on the replacement socket
- **AND** produces exactly one terminal event, one terminal request log, and one API-key settlement

#### Scenario: Typed transport send failure races a reader claim

- **WHEN** a typed transport send failure occurs after the reader has claimed the same sent turn
- **THEN** the sender does not replay the ambiguously delivered turn
- **AND** transfers the reader claim to one registered finalization task before awaiting it
- **AND** produces exactly one `response.failed` with the typed transport error
- **AND** releases or settles the API-key reservation and persists the terminal request log exactly once

### Requirement: Terminal WebSocket work has explicit cancellation-safe ownership

After an upstream message is received, processing and downstream delivery MUST be owned by a registered task before terminal handling can remove its request state from the pending queue. Reader or scope cancellation MUST NOT orphan that task. The reader MUST wait for owned terminal work only within the remaining shared application deadline; when no application drain is active, normal scope cancellation MUST instead use the existing bounded task-cancellation timeout. Shutdown persistence drain MUST observe both terminal-message and transport-end child tasks plus any request-log or settlement follow-up work they create.

#### Scenario: Cancellation lands after terminal state leaves pending

- **WHEN** a terminal event removes its request state from the pending queue and the upstream reader is then cancelled before settlement or downstream delivery completes
- **THEN** the reader waits for the owned terminal task within the remaining shared application deadline before propagating cancellation
- **AND** actual usage is settled exactly once
- **AND** exactly one terminal event and one terminal request log are produced

#### Scenario: Cancellation lands before a terminal event

- **WHEN** a Responses scope is cancelled while its request state remains pending or staged for transparent replay
- **THEN** its API-key reservation is released exactly once
- **AND** exactly one cancelled request log is produced against the last upstream account that owned the turn

#### Scenario: Cancellation lands after a pending batch is claimed

- **WHEN** terminal cleanup removes pending request states from the shared queue
- **THEN** it atomically transfers them to a registered, shielded finalization task before releasing the queue lock
- **AND** caller cancellation waits only within the remaining shared deadline without cancelling that sole child owner
- **AND** persistence drain continues to observe the child
- **AND** each request releases its turn admission, account-local create lease, API-key reservation, and create gate and persists its terminal log exactly once

#### Scenario: Pending account-health failure preserves settlement ordering

- **GIVEN** a keyed WebSocket turn is claimed by terminal cleanup
- **WHEN** the failure would record an account-health penalty
- **THEN** every claimed API-key reservation release commits before the account-health write
- **AND** every claimed terminal request log is handed to tracked persistence before the account-health write
- **AND** a failed or indeterminate reservation release prevents that account-health write

#### Scenario: Upstream terminal event preserves account-health ordering

- **GIVEN** a keyed WebSocket turn receives its upstream terminal event
- **WHEN** finalization would write account health
- **THEN** API-key settlement commits and the terminal request log is handed to tracked persistence before that health write
- **AND** a failed or indeterminate settlement or request-log handoff prevents the health write
- **AND** an account-health write failure does not prevent terminal downstream delivery

#### Scenario: Reader cancellation occurs outside process drain

- **WHEN** a reader is cancelled while its owned terminal task ignores cancellation and no application drain deadline exists
- **THEN** the reader waits only for the existing bounded task-cancellation timeout
- **AND** the owned task remains registered for eventual result consumption

#### Scenario: Active turn exceeds the shared deadline

- **WHEN** terminal processing remains blocked past the application drain deadline
- **THEN** Uvicorn proceeds with bounded connection and task shutdown
- **AND** the process does not start another application drain timeout

#### Scenario: Cancelled reader requires transport close

- **WHEN** scope cleanup cancels an upstream reader whose receive operation waits for transport close before propagating cancellation
- **THEN** cleanup first transfers its local replay and request-state owners to one registered finalization task
- **AND** requests reader cancellation exactly once
- **AND** closes the upstream transport before awaiting the already-cancelled reader
- **AND** bounds that await, lease release, and remaining terminal cleanup by the shared deadline
- **AND** expiry of the caller's wait does not cancel that sole cleanup owner
- **AND** produces exactly one terminal request log and one reservation settlement or release

#### Scenario: Connection lease release fails during scope cancellation

- **WHEN** scope cancellation owns a pending turn and releasing the upstream connection lease fails
- **THEN** request finalization completes before connection-lease release is attempted
- **AND** turn admission, account-local create lease, API-key reservation, create gate, and terminal request log are finalized exactly once
- **AND** the lease failure is reported without replacing the original scope cancellation

### Requirement: Owned launchers preserve shutdown semantics

The project CLI MUST use the pre-connection drain server with exactly one worker per process while preserving Uvicorn's startup-failure exit status and clean KeyboardInterrupt behavior. Every supported server launch path shipped or documented by the project MUST delegate to that owned CLI rather than invoking raw FastAPI or Uvicorn startup. Development Compose source synchronization MUST restart the owned server instead of relying on Uvicorn's incompatible reload launcher. Ambient `WEB_CONCURRENCY` MUST NOT create an unsupported multiprocess launch. The project MUST declare a Uvicorn version whose launcher API includes `Config.load_app()`. An embedded metrics server MUST NOT replace the main server's process signal handlers. During shutdown, after the shared application drain deadline, the owned server MUST stop awaiting Uvicorn connection and lifespan cleanup after 25 seconds. If that bound expires, it MUST terminate with the most recently captured shutdown signal, or SIGTERM when shutdown was programmatic, rather than return a cancellation-resistant cleanup task to asyncio runner teardown.

#### Scenario: Lifespan startup fails

- **WHEN** Uvicorn does not reach its started state
- **THEN** the project CLI exits with Uvicorn's startup-failure status

#### Scenario: Metrics endpoint is enabled

- **WHEN** the main process starts the embedded metrics server
- **THEN** only the main application server owns SIGTERM and SIGINT handlers

#### Scenario: Launcher dependency is resolved

- **WHEN** the project runtime dependencies are resolved
- **THEN** Uvicorn versions older than 0.47.0 are rejected

#### Scenario: Ambient worker count is greater than one

- **WHEN** `WEB_CONCURRENCY` requests multiple workers
- **THEN** the owned launcher still starts exactly one worker for the instance

#### Scenario: Operator follows a shipped or documented launch path

- **WHEN** the server is started through a project Compose file or documented local command
- **THEN** that path delegates to the owned pre-connection drain launcher
- **AND** direct SIGTERM commits the barrier before Uvicorn closes connections
- **AND** development source synchronization restarts that owned launcher

#### Scenario: Lifespan cleanup blocks after application drain

- **WHEN** Uvicorn connection or lifespan cleanup remains blocked after the shared drain phase
- **THEN** the owned launcher cancels and stops waiting after 25 seconds
- **AND** terminates with the most recently captured signal, or SIGTERM when no signal initiated shutdown
- **AND** does not leave cancellation-resistant cleanup registered for unbounded asyncio runner teardown
- **AND** Helm termination grace reserves two seconds for failed preStop start plus 30 seconds after the application deadline, leaving five seconds after the cleanup bound for process exit before SIGKILL
