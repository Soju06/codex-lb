## ADDED Requirements

### Requirement: Confirmed usage reset recovery

After an accepted reset-credit redemption, every managed-account consume path SHALL refresh usage and, when the window remains exhausted, send at most one minimal account-pinned wake request followed by a usage refresh. The request SHALL retain the resolved per-account proxy binding and SHALL fail closed when that route is unavailable. A fresh sample already proving a reset SHALL avoid the wake request. The original successful redemption SHALL remain successful if recovery fails.

The system SHALL clear a previous quota block and its cooldown only when corresponding quota samples prove that usage dropped below exhaustion and reset_at advanced by at least 60 seconds, and every applicable reported window has available quota. The write SHALL compare the previous status, reset deadline, and block marker so a newer block or hard-disabled state wins. A confirmed operator reset SHALL override the old quota cooldown. Weekly-only accounts SHALL be recoverable from their secondary window. Usage still full or a timestamp unchanged SHALL NOT prove a reset.

#### Scenario: Reset credit wakes a stale weekly limiter

- **GIVEN** a rate-limited account with exhausted weekly quota
- **WHEN** redemption succeeds but the first usage fetch remains stale
- **THEN** one routed wake request and a second fetch are attempted
- **AND** confirmed reopened quota clears the old block and runtime cooldown
- **AND** the account summary and standard quota selection agree

#### Scenario: Recovery cannot erase a newer failure

- **GIVEN** a usage reset refresh is in progress
- **WHEN** a newer rate limit or a pause, reauthentication requirement, or deactivation is recorded
- **THEN** the older reset result does not clear that state

### Requirement: Bounded stale rollover wake

When usage synchronization observes an exhausted quota window whose reset_at has elapsed, the system SHALL claim one wake attempt per account and window boundary before dispatch. Claims SHALL survive refresh ticks and concurrent workers. The wake SHALL use the same account proxy contract as explicit reset recovery. Transport failures and unchanged samples SHALL NOT trigger an unbounded retry loop. Hard-disabled accounts SHALL NOT be probed.

#### Scenario: Repeated stale weekly samples

- **GIVEN** upstream repeatedly returns the same exhausted, elapsed weekly window
- **WHEN** multiple usage synchronization passes or workers observe it
- **THEN** at most one claimed wake request is sent for that boundary
- **AND** a later window boundary can have a new attempt
