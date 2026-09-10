## ADDED Requirements

### Requirement: Per-request detached-session retire sweep bounds its lock wait

The fail-safe sweep that reconsiders detached HTTP-bridge generations on every bridge request MUST bound how long it waits for any single detached session's `pending_lock`. When the bound elapses the sweep MUST skip that session for the current pass, emit a warning, leave the session tracked and its lock state untouched, and continue. Session lifecycle owners (drain, close, cooldown-suppression retirement) MUST keep waiting for the lock without a bound so retirement decisions stay authoritative.

#### Scenario: Busy detached lock does not park the request path

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is held by another task for longer than the bound
- **WHEN** a request runs the fail-safe sweep
- **THEN** the sweep returns after the bound without closing the session
- **AND** a warning names the skipped session
- **AND** the lock remains owned by its holder with no stranded waiter

#### Scenario: Free detached lock still retires

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is free and which no turn owns
- **WHEN** a request runs the fail-safe sweep
- **THEN** the session is retired exactly as before

#### Scenario: Lifecycle owners keep the unbounded wait

- **GIVEN** a drain or close path calls the retire check without a bound while another task briefly holds the lock
- **WHEN** the holder releases
- **THEN** the retire check proceeds and retires the session
