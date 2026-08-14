## ADDED Requirements

### Requirement: Durable bridge claims fence out the retiring predecessor

A successful `claim_live_session` over an existing durable row MUST advance the owner epoch, including when the claiming instance already owns the row, so fenced updates issued by the predecessor local session — its release and any outstanding renewals — no-op after the claim instead of racing the successor. The claim's write MUST be authoritative: it MUST set every ownership field (owner, process epoch, owner epoch, lease, state, account) unconditionally, so a concurrent write committing between the claim's read and its commit cannot survive into the claim's result. A claim that returns successfully MUST reflect the claimant as the live owner. Concurrent claims over the same row MUST serialize on the epoch: the write MUST land only if the epoch still matches the claim's read, and a losing claim MUST retry against fresh state (within a bounded budget), so two claimants can never hold colliding fences. The snapshot a claim returns MUST be the state that claim itself wrote — not a post-commit re-read, which a later claim's commit could have already overwritten with its own epoch.

#### Scenario: A successor claim fences the predecessor's release

- **GIVEN** a retiring bridge session and a successor session claiming the same durable row on the same instance
- **WHEN** the successor's claim commits before the predecessor's release lands
- **THEN** the release is fenced out by the advanced epoch and the row stays ACTIVE and owned by the instance

#### Scenario: A release committing mid-claim does not corrupt the claim

- **GIVEN** the predecessor's release commits between the successor claim's read and its write
- **WHEN** the claim commits
- **THEN** the claim's result reflects the claimant as the live owner with the advanced epoch
- **AND** the request proceeds instead of failing with `bridge_instance_mismatch`

#### Scenario: Racing successor claims cannot share an epoch

- **GIVEN** two successor claims that both read the same owner epoch before either writes
- **WHEN** both commit
- **THEN** they land on distinct epochs, with the loser retrying against fresh state

#### Scenario: Foreign-claim rejection is unchanged

- **GIVEN** a durable row owned by another instance with a live lease
- **WHEN** a claim without takeover permission runs
- **THEN** the owner and lease remain unchanged, as before
