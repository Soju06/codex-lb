## ADDED Requirements

### Requirement: Completion cleanup distinguishes local failures from durable adoption

A completed HTTP bridge response MUST preserve local quarantine failures recorded after completion processing begins, including the first eventless strike and replacement-session evidence. Successful settlement and fresh-anchor registration MUST retain existing immediate cleanup of durable-only poison adopted for that settlement. Removing that evidence MUST NOT extend or discard an intervening local failure's own deadline or count. Failed settlement or registration MUST retain existing poison protection. Quarantine capacity, TTL and unknown-key behavior MUST remain unchanged.

#### Scenario: Local failure during completion
- **WHEN** a local failure is recorded while completion waits for its pending lock, loads durable state or registers continuity
- **THEN** cleanup preserves that failure's quarantine or strike behavior

#### Scenario: Retry adopts durable-only poison
- **WHEN** the initial load fails and settlement's retry adopts durable-only poison
- **AND** settlement and fresh-anchor registration succeed
- **THEN** the adopted poison is cleared immediately and intervening local evidence retains its own deadline and count

#### Scenario: Replacement owns the key
- **WHEN** a predecessor completes after another session records evidence under the same key
- **THEN** predecessor cleanup does not modify replacement evidence or its quarantine marker, even after entry eviction and recreation

#### Scenario: Settlement or registration fails
- **WHEN** completion adopts durable poison but settlement or fresh-anchor registration fails
- **THEN** existing protection against the old anchor remains

### Requirement: Verified replay cleanup retains origin authority

A verified stale-anchor replay MUST preserve local failures recorded after it captures its origin's quarantine evidence. When the replay completes under its origin key, durable loading and revocation MUST use that same authority before final cleanup.

#### Scenario: First strike during replay
- **WHEN** a first strike is recorded after origin capture and before replay completion
- **THEN** replay cleanup retains the strike, including when the origin capture observed absence or poison

#### Scenario: Same-key replay adopts durable disproof
- **WHEN** a same-key replay's completion load disproves durable poison after a local failure was recorded during replay
- **THEN** loading and final cleanup preserve the intervening local failure's own deadline and count
