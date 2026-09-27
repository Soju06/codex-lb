## ADDED Requirements

### Requirement: Compact recorded plan duration in account selectors

The original Detail selector MUST show recorded plan time immediately beneath each account status badge. List rows MUST also show a compact duration. Positive durations MUST use zero-padded whole days and remaining whole hours with literal d/h suffixes, such as `18d 08h`; positive periods below an hour MUST display `00d 00h`. Unknown deadlines MUST show an unavailable label, and elapsed deadlines MUST show a distinct elapsed label without changing status. Compact displays MUST expose recorded deadline, last check when available and recorded-period interpretation through their title or accessible description, while the selected detail retains full visible metadata. They MUST use the shared page clock and update at least each minute without issuing per-account requests.

#### Scenario: Duration beneath the active badge
- **WHEN** the Detail selector has an Active account with 18 days and 8 hours remaining
- **THEN** `18d 08h` appears immediately below Active
- **AND** account selection still updates the original inline statistics and charts

#### Scenario: Compact duration rolls over an hour boundary
- **WHEN** a displayed positive period falls below one hour
- **THEN** the compact display shows `00d 00h` until its deadline
- **AND** at the deadline it changes to an elapsed label rather than a negative duration

#### Scenario: Missing metadata has no inferred duration
- **WHEN** subscription metadata is unavailable but access-token expiry is known
- **THEN** the compact display shows an unavailable label without using token expiry
