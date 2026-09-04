## ADDED Requirements

### Requirement: Browser OAuth callback listener follows live pending-flow lifetime

Each replica that starts dashboard browser OAuth SHALL own deadline-driven cleanup for its process-local callback listener. When no unexpired pending browser flow remains on that replica, it MUST prune the expired local flow state and release the callback listener without requiring a later callback, status request, completion request, reset, or new OAuth start. The listener MUST remain available while at least one unexpired pending browser flow still depends on it.

Starting a new browser flow concurrently with deadline cleanup MUST either reuse a listener that is not stopping or wait for the prior listener to finish stopping and install a replacement. Retiring cleanup work MUST NOT clear or stop a replacement listener or replacement deadline task.

Callback-listener startup and shutdown MUST remain process-owned across request cancellation and full-store reset. A browser flow MUST NOT expose an untracked listener or remain pending locally without deadline ownership. A browser flow that becomes terminal through a callback or durable reconciliation MUST begin the same no-pending listener cleanup without waiting for its original deadline.

Process-owned listener cleanup MUST retry transient shutdown failures without requiring another request. Each cleanup task MUST bound its retry batch and preserve listener ownership if every attempt fails. Exhaustion MUST propagate to an awaiting caller, or be written to the process error log when no request waiter owns the cleanup. A subsequent browser start or full-store reset MUST initiate a fresh bounded cleanup batch before publishing any replacement listener. A full-store reset MUST fence both an in-flight browser-start persistence write and an in-flight durable reconciliation read so work begun before the reset cannot republish local flow state, a listener, or deadline work afterward. Once account tokens have been persisted from a successful callback, cancellation of that callback request MUST NOT interrupt the corresponding local terminal transition and listener cleanup.

#### Scenario: Sole abandoned browser flow expires

- **GIVEN** a replica started one browser OAuth flow and its callback listener is running
- **WHEN** that flow reaches its expiry without completing
- **THEN** the replica removes the expired flow and state-token lookup from local runtime state
- **AND** the replica stops the callback listener without any subsequent OAuth or dashboard request

#### Scenario: Overlapping browser flows retain the shared listener

- **GIVEN** two pending browser OAuth flows on one replica have different expiry deadlines and share one callback listener
- **WHEN** the earlier flow expires
- **THEN** the expired flow is removed but the listener remains available for the later flow
- **AND** deadline cleanup continues until the later flow expires or completes

#### Scenario: Final overlapping browser flow expires

- **GIVEN** an earlier browser flow has expired while a later browser flow kept the shared listener alive
- **WHEN** the later flow also expires without completing
- **THEN** the replica stops the listener without waiting for another callback or request

#### Scenario: Browser start races listener expiry cleanup

- **GIVEN** deadline cleanup has begun stopping the callback listener
- **WHEN** a new browser OAuth flow starts concurrently
- **THEN** the new flow does not reuse the stopping listener
- **AND** when the callback port is available, a live replacement listener is installed before the start returns
- **AND** cleanup belonging to the prior listener cannot clear or stop the replacement

#### Scenario: Final browser flow completes before expiry

- **GIVEN** a pending browser OAuth flow owns the callback listener and deadline cleanup task
- **WHEN** that final pending flow completes before its expiry
- **THEN** the replica stops the callback listener without waiting for the original deadline
- **AND** it cancels and drains the obsolete deadline cleanup work
- **AND** that retired work cannot affect a later listener or deadline task

#### Scenario: Browser callback reports a terminal error

- **GIVEN** one pending browser flow owns the callback listener
- **WHEN** the provider callback returns a terminal OAuth error
- **THEN** the callback response completes
- **AND** the replica cancels the obsolete deadline task and releases the listener before the original deadline

#### Scenario: Listener shutdown fails transiently

- **GIVEN** the final local browser flow has expired or become terminal
- **WHEN** the first attempt to stop its callback listener fails transiently
- **THEN** the process-owned cleanup task keeps the listener tracked and retries without another request
- **AND** no replacement listener is published until cleanup succeeds

#### Scenario: Listener shutdown keeps failing

- **GIVEN** callback-listener shutdown fails on every attempt in one cleanup task
- **WHEN** that task exhausts its bounded retry batch
- **THEN** an awaiting caller receives the terminal shutdown error, or an unattended cleanup reports it to the process error log
- **AND** the listener remains tracked until a subsequent browser start or full-store reset initiates a fresh bounded cleanup batch
- **AND** no replacement listener is published while the prior listener may remain bound

#### Scenario: Durable terminal is reconciled on the origin replica

- **GIVEN** another replica durably completed a browser flow while the origin still holds it as pending
- **WHEN** the origin reconciles that terminal through status or completion polling
- **THEN** the origin begins listener cleanup immediately when no other browser flow remains pending

#### Scenario: Browser-start request is canceled during listener startup

- **GIVEN** a browser flow, listener startup, and deadline cleanup are registered in the process-local store
- **WHEN** the requesting task is canceled before listener startup finishes
- **THEN** listener startup remains owned by the store
- **AND** the pending flow retains deadline cleanup ownership

#### Scenario: Successful callback request is canceled after token persistence

- **GIVEN** a callback has durably persisted valid account tokens for a browser flow
- **WHEN** its request task is canceled before local terminal cleanup finishes
- **THEN** the store-owned terminal transition still marks the local flow successful
- **AND** the listener remains available only for any other pending browser flows

#### Scenario: Durable reconciliation overlaps full-store reset

- **GIVEN** durable reconciliation began reading a browser flow before a full-store reset
- **WHEN** reset completes before that read returns
- **THEN** the stale read does not republish the flow or any deadline task into the reset store

#### Scenario: Browser persistence overlaps full-store reset

- **GIVEN** browser-start persistence began before a full-store reset
- **WHEN** reset completes before that persistence returns
- **THEN** the stale start does not publish its flow, listener, startup task, or deadline task

#### Scenario: Reset overlaps listener startup

- **GIVEN** a process-local callback listener is still starting
- **WHEN** full OAuth store reset begins
- **THEN** reset waits for startup to settle before stopping the listener
- **AND** no listener, flow, startup task, stop task, or deadline task remains owned after reset completes
