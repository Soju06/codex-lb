## ADDED Requirements

### Requirement: SQLite redeem-claim cleanup survives repeated cancellation

After a process acquires the SQLite reset-credit redeem claim, the system MUST
treat heartbeat cancellation and drain followed by holder-fenced claim release
as one owned cleanup operation. Repeated caller cancellation while cleanup is
suspended MUST NOT interrupt that operation. Deferred cancellation MUST surface
only after heartbeat shutdown and release finish. Lease expiry MUST remain the
crash or release-error backstop, not routine live-process cancellation cleanup.

#### Scenario: Repeated cancellation cannot strand a live SQLite claim

- **GIVEN** a SQLite redemption holds a durable claim with a heartbeat
- **WHEN** the body is cancelled and cancellation is delivered again after
  claim release starts
- **THEN** the heartbeat is cancelled and drained
- **AND** holder-fenced release finishes before cancellation surfaces
- **AND** a successor can acquire immediately without waiting for lease expiry
