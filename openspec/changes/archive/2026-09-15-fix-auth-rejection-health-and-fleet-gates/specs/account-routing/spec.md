## MODIFIED Requirements

### Requirement: Rejection repair depends on changed access-token material

A guarded token rotation MUST NOT clear proven access rejection solely because encryption produces different ciphertext for unchanged access-token material. Replacing only refresh or ID token material MUST NOT restore access-token eligibility or suppress a pending rejection of unchanged access material. Credential comparison and the resulting write MUST remain fenced against concurrent token replacement.

#### Scenario: Refresh returns the rejected access token again
- **WHEN** refresh persists newly encrypted copies of the same rejected access token
- **THEN** the account retains its rejection reason and remains excluded locally and after a routing snapshot refresh

#### Scenario: Refresh-only rotation precedes rejection retry
- **WHEN** rotation replaces refresh-token material but retains the rejected access-token material before rejection persistence retries
- **THEN** the rejection MUST persist against the latest token ciphertexts
- **AND** genuine access-token replacement during that retry MUST prevent a stale rejection write

### Requirement: Concurrent health writes do not erase proven credential rejection

When a rejection write loses a compare-and-set because noncredential health fields changed, the proxy MUST re-read current state and retry while the rejected access-token material remains current. It MUST preserve concurrent reset and blocked timestamps and MUST NOT override an operator pause, deactivation, or repaired access-token material. Conversely, later health and cooldown updates from in-flight requests MUST NOT erase a committed proven access rejection or restore the same rejected credentials to selection or bridge reuse. Cooldown evidence MUST remain available for a subsequent genuine credential repair.

#### Scenario: Rate limiting commits before rejection persistence
- **WHEN** another request changes the rejected account's cooldown without changing credentials before the rejection write
- **THEN** the rejection is persisted while the concurrent cooldown timestamps are retained
- **AND** the account remains excluded after that cooldown expires

#### Scenario: A peer repairs credentials before retry
- **WHEN** the fresh state contains replacement access-token material
- **THEN** the stale rejection does not overwrite that state or mark the repaired account unavailable

#### Scenario: Health updates follow a committed rejection
- **WHEN** an older in-flight request reports rate limiting, quota exhaustion, or recovery after access rejection commits
- **THEN** the rejected account MUST remain unavailable after routing snapshot refresh and cooldown expiry
- **AND** applicable cooldown timestamps MUST be retained without replacing the blocking authentication state
