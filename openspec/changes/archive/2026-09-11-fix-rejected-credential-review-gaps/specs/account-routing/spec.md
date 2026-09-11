## ADDED Requirements

### Requirement: Rejection repair depends on changed access-token material

A guarded token rotation MUST NOT clear proven access rejection solely because encryption produces different ciphertext for unchanged access-token material. Replacing only refresh or ID token material MUST NOT restore access-token eligibility. Credential comparison and the resulting write MUST remain fenced against concurrent token replacement.

#### Scenario: Refresh returns the rejected access token again
- **WHEN** refresh persists newly encrypted copies of the same rejected access token
- **THEN** the account retains its rejection reason and remains excluded locally and after a routing snapshot refresh

### Requirement: Concurrent health writes do not erase proven credential rejection

When a rejection write loses a compare-and-set because noncredential health fields changed, the proxy MUST re-read current state and retry while the rejected credential generation remains current. It MUST preserve concurrent reset and blocked timestamps and MUST NOT override an operator pause, deactivation, or repaired credential generation.

#### Scenario: Rate limiting commits before rejection persistence
- **WHEN** another request changes the rejected account's cooldown without changing credentials before the rejection write
- **THEN** the rejection is persisted while the concurrent cooldown timestamps are retained
- **AND** the account remains excluded after that cooldown expires

#### Scenario: A peer repairs credentials before retry
- **WHEN** the fresh state contains replacement credentials
- **THEN** the stale rejection does not overwrite that state or mark the repaired account unavailable

### Requirement: Direct dispatch honors credential availability

Warmup in every targeting mode and automation dispatch, whether manual or scheduled, MUST exclude reauthentication accounts with proven access rejection or known expired access credentials. Refresh-only warnings with usable or unknown-expiry access credentials MUST retain existing eligibility subject to independent gates.

#### Scenario: Direct consumer targets a rejected account
- **WHEN** warmup or an automation targets an account carrying proven access rejection
- **THEN** no upstream request is sent using that account

#### Scenario: Direct consumer targets a refresh-only warning
- **WHEN** warmup or an automation targets a refresh-only warning account whose access token is not known expired
- **THEN** credential availability alone does not exclude it
