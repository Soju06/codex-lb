## ADDED Requirements

### Requirement: Bridge session pending_lock critical sections do not suspend on settings or database reads

Critical sections holding an HTTP bridge session's `pending_lock` MUST NOT await settings-cache reads or database queries. Inputs that require such reads (the keyed fair-share congestion threshold for a stream-lease reacquire) MUST be resolved before the lock is acquired and passed into the lock-holding path as a snapshot. A stalled settings or database read MUST therefore stall only the request performing it, never the session's `pending_lock` — interruption cleanup and queue bookkeeping on that session remain able to acquire the lock.

#### Scenario: Stalled settings refresh does not wedge the session lock

- **GIVEN** a keyed bridge submit whose settings-cache refresh stalls on a hung database query
- **WHEN** the submit is waiting on that refresh
- **THEN** the submit has not yet acquired the session's `pending_lock`
- **AND** other work on the session (interruption cleanup, queue bookkeeping) can acquire the lock promptly

#### Scenario: Reacquire with a provided snapshot performs no settings read under the lock

- **GIVEN** a keyed session whose stream lease is reacquired under `pending_lock` with a fair-share threshold snapshot provided by the caller
- **WHEN** the reacquire runs
- **THEN** it does not read the settings cache
- **AND** the provided threshold is forwarded to lease acquisition unchanged

#### Scenario: Fair-share admission semantics are preserved

- **WHEN** a keyed warm-session turn is admitted through the pre-lock snapshot path
- **THEN** the same congestion threshold source governs the fair-share gate as before
- **AND** a denial raises the same `api_key_stream_fair_share` local-cap envelope
