## ADDED Requirements

### Requirement: A completed SQLite teardown is never reclaimed

When the initial bounded wait for file-backed SQLite rollback or close expires, the service MUST observe the existing teardown task for a bounded completion grace before reclamation. Only successful task completion observed within that opportunity MUST exempt the session from fencing, driver interruption, connection invalidation and deferred cleanup registration. Normal remaining teardown MUST still run. Failed, cancelled or still-pending tasks MUST retain the existing fencing and tracked reclamation/late-cleanup ownership, using connections captured before teardown; already-closed handles MUST not be interrupted or invalidated again.

A successful-completion warning MUST name the phase, configured initial bound and elapsed time measured at that bound before grace. Diagnostics MUST describe observed task/cleanup outcomes; elapsed time alone MUST NOT be reported as measured event-loop lag, and failed invalidation alone MUST NOT be asserted to prove a permanent writer hold. Failed teardown and failed invalidation MUST remain visible at warning level through the existing diagnostic owner.

#### Scenario: Real rollback worker completes while the event loop is delayed
- **GIVEN** a file-backed SQLite write transaction whose native rollback finishes while the event loop is blocked across the initial bound
- **WHEN** grace observes successful task completion after the loop resumes
- **THEN** the session is not fenced and no connection is interrupted or invalidated
- **AND** no deferred cleanup is registered, normal close runs, and another writer can commit
- **AND** the warning reports the phase, initial bound and pre-grace elapsed time without claiming measured lag

#### Scenario: Real close worker completes while the event loop is delayed
- **GIVEN** a file-backed SQLite session whose native close finishes while completion callbacks cannot run across the initial bound
- **WHEN** grace observes successful close completion
- **THEN** no reclaim or deferred cleanup is performed and another writer can commit

#### Scenario: Failure or cancellation is not successful completion
- **WHEN** a teardown that outlived the initial bound fails, is cancelled, or remains pending after grace
- **THEN** existing fencing, captured-connection reclamation attempts and owned late cleanup remain in effect
- **AND** already-closed handles are skipped without suppressing an observed teardown failure

#### Scenario: Reclamation diagnostics do not invent a permanent lock
- **WHEN** invalidating a captured open connection fails
- **THEN** the failure is reported at warning level
- **AND** diagnostics do not assert that a permanent writer hold has been proven
