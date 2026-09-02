## ADDED Requirements

### Requirement: A completed SQLite teardown is never reclaimed

The bounded file-backed SQLite teardown measures its deadline in wall-clock
time, so a starved event loop can reach that deadline while the aiosqlite
worker has already finished and only its completion remains queued. Reclaiming
there fences a healthy session and invalidates a live connection.

When the bound expires, the service MUST observe the abandoned rollback or
close for a bounded grace period before acting. When that teardown has
completed successfully, the service MUST NOT fence the session, register the
task for deferred cleanup, interrupt the driver, or invalidate the connection,
and MUST emit exactly one warning naming the phase, the configured bound, and
the measured elapsed time.

A terminal task is not by itself proof of a successful teardown. A teardown
that failed or was cancelled MUST NOT receive this exemption, and a teardown
still pending after the grace MUST retain the existing fence, cleanup
registration, driver interrupt, connection invalidation, and late-completion
bookkeeping. A failed connection invalidation MUST be reported at warning
level, because it is the point at which a permanent writer hold begins.

#### Scenario: Rollback completes after the bound elapses

- **GIVEN** a file-backed SQLite session whose rollback outlives the teardown bound
- **WHEN** that rollback completes successfully before the reclaim acts
- **THEN** the session is not fenced and its connection is neither interrupted nor invalidated
- **AND** one warning reports the phase, bound, and elapsed seconds
- **AND** the session's normal close still runs

#### Scenario: Close completes after the bound elapses

- **GIVEN** a file-backed SQLite session whose close outlives the teardown bound
- **WHEN** that close completes successfully before the reclaim acts
- **THEN** no reclaim is performed and no deferred cleanup is registered

#### Scenario: Failed or cancelled teardown still reclaims

- **GIVEN** a file-backed SQLite session whose rollback outlives the teardown bound
- **WHEN** that rollback ends by raising or by being cancelled
- **THEN** the session is fenced and its connection is invalidated as before
