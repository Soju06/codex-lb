## ADDED Requirements

### Requirement: Durable bridge claims fence out the retiring predecessor

A successful `claim_live_session` over an existing durable row MUST advance the owner epoch, including when the claiming instance already owns the row, so fenced updates issued by the predecessor local session — its release and any outstanding renewals — no-op after the claim instead of racing the successor. The claim's write MUST be authoritative: it MUST set every ownership field (owner, process epoch, owner epoch, lease, state, account) unconditionally, so a concurrent write committing between the claim's read and its commit cannot survive into the claim's result. A claim that returns successfully MUST reflect the claimant as the live owner. A session creator that has already lost its inflight registry slot MUST NOT claim the durable row at all: claiming would advance the epoch past the session that won and fence that winner's own renewals out of a row it legitimately owns. A creator that nonetheless fails to register — because another session already holds the registry slot for its key, or because a replacement creator holds the in-flight slot and has not published its session yet — MUST hand the epoch it claimed to that registered session when both point at the same row and selected the same account. When the registered session selected a DIFFERENT account it no longer shares the row — the claim rewrote the row's account binding and cleared its continuity aliases — so the creator MUST release the row rather than preserve it, letting that session be fenced promptly instead of dispatching against a row bound elsewhere, so the winner's renewals keep matching, and MUST NOT release the durable row. Independently of that handoff, a renewal fenced by an epoch advance from THIS process — matching the instance ID, the owner process epoch, and the row's account binding — MUST adopt the newer epoch when the renewing session still holds the registry slot for its key — that is a superseded creator, not an ownership loss — while a session whose slot a different local session holds MUST still be evicted with the existing retryable instance-mismatch contract: it claimed last, so its epoch is current and its fenced release would otherwise close the row out from under the registered session. Concurrent claims over the same row MUST serialize on the epoch: the write MUST land only if the epoch still matches the claim's read, and a losing claim MUST retry against fresh state (within a bounded budget), so two claimants can never hold colliding fences. A claim that loses to a concurrent writer MUST revalidate its takeover permission against the fresh read rather than reusing the caller's pre-claim decision, so a loser cannot steal the winner's now-live lease; a live foreign owner then fails closed and the real owner is reported. A caller retrying a claim MUST NOT restore takeover permission against a live foreign owner either, so the fail-closed outcome survives the retry rather than being undone by a fresh claim. The snapshot a claim returns MUST be the state that claim itself wrote — not a post-commit re-read, which a later claim's commit could have already overwritten with its own epoch.

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

#### Scenario: A losing claimant does not steal the winner's lease

- **GIVEN** two replicas recovering the same released row, both permitted to take over
- **WHEN** one wins and the other re-reads the winner's now-live lease
- **THEN** the loser fails closed and reports the winner as owner instead of claiming the row
- **AND** the caller does not retry the claim with takeover permission against that live owner

#### Scenario: A rejected creator leaves the registered winner's row alone

- **GIVEN** an inflight waiter was evicted and a replacement session won the registry slot
- **WHEN** the stale creator finishes creating its session
- **THEN** it does not claim the durable row, so the winner's epoch is untouched
- **AND** it closes its own session without releasing the durable row, leaving the winner's row live
- **AND** if it had already claimed (eviction landing during the claim), the winner adopts that epoch so its renewals keep matching

#### Scenario: The registered session adopts a same-instance epoch advance

- **GIVEN** a session still holding the registry slot for its key whose durable row was advanced by this instance
- **WHEN** its lease renewal is fenced by that newer epoch
- **THEN** it adopts the epoch and keeps renewing instead of being evicted

#### Scenario: A newer process incarnation still fences the predecessor

- **GIVEN** two process incarnations sharing a configured instance ID across a graceful restart
- **WHEN** the successor claims and the predecessor's session renews
- **THEN** the predecessor is evicted rather than adopting the successor's epoch

#### Scenario: An advance that rebound the row to another account is not adopted

- **GIVEN** a registered session whose durable row was advanced and rebound to a different account
- **WHEN** its renewal is fenced by that advance
- **THEN** it is evicted rather than adopting the epoch, so it never dispatches on the other account's row

#### Scenario: A session that lost its slot is still evicted

- **GIVEN** a session whose registry slot is now held by a different local session
- **WHEN** its renewal is fenced
- **THEN** it is evicted and the retryable instance-mismatch error is raised

#### Scenario: A replacement that has not published yet is still the winner

- **GIVEN** a replacement creator holds the in-flight slot and has claimed but not yet registered its session
- **WHEN** the stale creator fails and settles
- **THEN** it does not release the durable row, which the replacement is about to publish against

#### Scenario: A row rebound away from the winner is released

- **GIVEN** a registered winner on one account and a stale creator whose claim rebound the row to another
- **WHEN** the stale creator settles
- **THEN** it releases the row instead of preserving it, so the winner is fenced promptly rather than dispatching against a row bound elsewhere

#### Scenario: A sole creator still releases its row

- **GIVEN** a failed creator with no registered session and no replacement in flight
- **WHEN** it settles
- **THEN** it releases the durable row rather than leaking it

#### Scenario: Foreign-claim rejection is unchanged

- **GIVEN** a durable row owned by another instance with a live lease
- **WHEN** a claim without takeover permission runs
- **THEN** the owner and lease remain unchanged, as before
