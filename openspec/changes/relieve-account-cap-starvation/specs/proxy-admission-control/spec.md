## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits, and the configured limits MUST be cluster-wide per-account targets enforced across all replicas rather than per-replica allowances. Because per-account caps are partitioned per replica via the bridge ring and cannot be safely partitioned across intra-pod worker processes, each instance MUST run a single worker process; horizontal scaling is achieved by adding replicas. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value.

When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. A bare process-session mapping MAY supply soft locality only while the request is self-contained, pre-visible, and has no required owner. Account-cap spillover MUST be decided during account selection and MUST NOT switch an account after a request enters shared transport, replay, or durable bridge ownership. Hard-continuity work MUST remain on its required owner and MAY fail closed when that owner is saturated. Hard Codex ownership rows MUST bypass soft sticky fallback/reallocation so pressure cannot delete or rewrite them.

An unanchored parallel fork bridge session whose payload is self-contained (no `previous_response_id`, no `conversation`, and no input file references) carries no continuity ownership; when its preferred account is rejected by a local account cap (`account_stream_cap` or `account_response_create_cap`) during session creation, the proxy MUST drop the preferred-account hint exactly once for that request and retry account selection among eligible accounts before entering the recoverable account-capacity wait. Payloads that carry any continuity owner signal MUST NOT spill and keep the existing preferred-owner behavior.

The recoverable account-capacity wait entered by bridged Responses session creation and recovery when every eligible account is capped MUST be bounded by a fixed 120-second ceiling in addition to the bridge request budget, anchored per request at the first capacity wait. When the bound expires the proxy MUST surface the original HTTP 429 local-cap error envelope (`account_stream_cap` or `account_response_create_cap`) instead of continuing to hold the request. Per-session response-create gate waits (`response_create_gate_timeout`) MUST remain bounded by the bridge request budget only.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a self-contained `/v1/responses` request has only bare process-session affinity to account A
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

#### Scenario: Late WebSocket cap race does not retire shared work

- **GIVEN** a request has entered an upstream WebSocket shared with another in-flight response
- **WHEN** a later account response-create lease acquisition loses a capacity race
- **THEN** the proxy rejects only the newly unadmitted request with the existing local-cap failure
- **AND** it does not retire or switch the shared upstream WebSocket to spill that request

#### Scenario: Existing bridge ownership is not replaced by cap spillover

- **GIVEN** a session header resolves to a live or durable HTTP bridge owner
- **WHEN** that owner's account or response-create gate is saturated
- **THEN** the request follows the existing hard bridge-capacity behavior
- **AND** account-cap spillover does not publish a replacement bridge under the same canonical identity

#### Scenario: Unanchored parallel fork spills off a capped preferred account

- **GIVEN** an unanchored parallel fork bridge session creation whose payload carries no previous response, conversation, or input file reference
- **AND** its preferred account is rejected with `account_stream_cap`
- **AND** another eligible account is below its stream cap
- **WHEN** session creation retries selection after dropping the preferred-account hint
- **THEN** the fork session is created on the eligible account instead of waiting on the capped account

#### Scenario: Owner-bearing fork payloads do not spill

- **GIVEN** a parallel fork bridge session creation whose payload carries a `previous_response_id`
- **WHEN** its preferred account is rejected with a local account cap
- **THEN** the preferred-account hint is kept and the existing preferred-owner behavior applies

#### Scenario: Account-capacity wait surfaces the cap error at the ceiling

- **GIVEN** every eligible account for a bridged Responses request stays at its stream cap
- **WHEN** the recoverable account-capacity wait exceeds the 120-second ceiling
- **THEN** the request fails with HTTP 429 and `error.code = "account_stream_cap"`
- **AND** the connection is still open to deliver the error envelope

#### Scenario: Gate contention is not bounded by the capacity ceiling

- **GIVEN** a bridged Responses request waiting on a per-session response-create gate held by an in-flight turn
- **WHEN** the wait exceeds the 120-second account-capacity ceiling
- **THEN** the gate wait continues, bounded by the bridge request budget only

### Requirement: Account concurrency caps are partitioned across live replicas

Each replica MUST derive its local share of every configured account concurrency cap deterministically from the sorted active bridge-ring member list: with `R` active members and this replica at rank `k` in instance-id order, the share MUST be `floor(cap / R)` plus one extra slot when `k < cap mod R`, floored at one slot so an account never becomes unroutable on a replica; a nonpositive configured cap MUST remain unlimited on every replica. Partition derivation MUST NOT add database reads to the request or admission path; it MUST refresh from bridge-ring registration and heartbeat ticks, and the observing replica MUST count itself even when its own ring row is missing or stale. Membership changes that cannot grow this replica's share of any cap MUST be adopted on the next refresh; membership changes that could grow this replica's share MUST NOT be adopted until that exact pending partition (member count and rank) has been observed continuously for the configured stability window. Whether a change could grow the share MUST be decided by comparing the prospective share against the current share for each configured cap (the response-create and stream limits actually in effect — the dashboard-configured overrides when present and otherwise the startup defaults, i.e. the same effective caps the admission path partitions, never the startup defaults when a dashboard override differs) using the same share formula the admission path enforces, and MUST NOT be decided from the direction of the member count or the rank alone: neither direction determines growth, because a member-count decrease can be outweighed by a rank increase and a rank decrease by a large enough member-count increase. A change MUST be deferred only when some configured cap's prospective share is strictly greater than its current share; a change whose every configured cap's prospective share is less than or equal to its current share MUST be adopted on the next refresh, whether the member count or rank rose or fell (for example a member-count decrease paired with a rank increase that reduces this replica's configured share, as when churn removes members while adding lower-sorting instance ids, MUST be adopted immediately rather than held). The stability window (`proxy_account_cap_partition_scale_down_seconds`, default 60 seconds, minimum 30) applies to deferred share-growing changes only; a change of the pending partition, including a rank change at an unchanged count, MUST restart the window. A failed membership read MUST retain the last adopted partition; while a share-growing change is pending, a failed read MUST also restart the stability window so the observation gap does not count toward the continuous-stable requirement. Setting `proxy_account_caps_scope` to `replica` MUST restore per-replica cap semantics, and a replica that observes no other active member MUST use the full configured caps.

For stream leases only, a replica whose local share is exhausted MAY additionally borrow from observed idle peer capacity: with fresh per-account in-flight stream-lease counts published by every other active ring member, the borrow allowance for an account MUST be `floor((configured stream cap − observed cluster in-flight) / R)`, floored at zero, where observed cluster in-flight is this replica's local in-flight count plus the sum of every peer's freshest published count for that account. Borrowing MUST be disabled — preserving the static share — whenever any other active member's published counts are missing or staler than the ring stale threshold, whenever the configured stream cap is nonpositive, whenever `proxy_account_caps_scope` is `replica`, and whenever the partition has a single member. The stream recovery reserve MUST still be subtracted from the borrowed allowance exactly as from the static share for non-recovery admissions. Borrowed admissions MUST be observable via a dedicated metric. Because published counts lag by up to one heartbeat interval, the aggregate MAY transiently exceed the configured cap after simultaneous borrows; the fair-fraction allowance MUST cap the sustained aggregate at the configured cap once published counts reflect actual usage, and the transient excess window MUST NOT exceed the ring heartbeat cadence that refreshes the counts.

#### Scenario: Shares sum to the configured cap

- **GIVEN** a configured account stream cap of 8
- **AND** three active replicas in the bridge ring
- **WHEN** each replica derives its share
- **THEN** the shares by ascending instance-id rank are 3, 3, and 2

#### Scenario: Cap smaller than the replica count keeps accounts routable

- **GIVEN** a configured account response-create cap of 2
- **AND** three active replicas
- **WHEN** each replica derives its share
- **THEN** every replica's share is at least 1

#### Scenario: Scale-up is adopted immediately

- **GIVEN** a replica whose adopted partition has replica count 2
- **WHEN** a refresh observes three active members
- **THEN** the replica adopts the three-way partition on that refresh

#### Scenario: A missed heartbeat does not inflate surviving shares

- **GIVEN** two active replicas and a scale-down stability window of 60 seconds
- **WHEN** one replica's heartbeat goes stale and recovers within the window
- **THEN** the surviving replica keeps its two-way share throughout
- **AND** the two-way partition is only replaced after the lower count is observed continuously for the full window

#### Scenario: Same-count churn does not grow a share early

- **GIVEN** three active replicas with this replica at rank 2 (cap 8 share is 2 slots) and a scale-down stability window of 60 seconds
- **WHEN** the other two replicas drain while later-sorting instance ids appear, keeping the member count at 3 but moving this replica to rank 0 so its cap-8 share would grow from 2 to 3
- **THEN** this replica keeps its previous rank's share until the churned membership has been observed continuously for the full window
- **AND** same-count churn that moves this replica to a later rank (shrinking every configured cap's share) is adopted on that refresh

#### Scenario: Mixed churn that grows the count but moves the rank earlier is deferred

- **GIVEN** a replica whose adopted partition is five members at rank 4 and a stability window of 60 seconds
- **WHEN** a refresh observes six members with this replica at rank 0
- **THEN** the replica keeps its adopted partition until the six-member rank-0 observation has been held continuously for the full window

#### Scenario: Count growth that shrinks the share is adopted immediately despite an earlier rank

- **GIVEN** a replica whose adopted partition is two members at rank 1
- **WHEN** a refresh observes three members with this replica at rank 0 (a rolling replacement where the lower-ranked member drains while two later-sorting ids appear)
- **AND** every configured cap's prospective share is no larger than the current share (for cap 8 the share drops from 4 to 3)
- **THEN** the replica adopts the three-member rank-0 partition on that refresh without waiting for the stability window

#### Scenario: Count decrease that shrinks the configured share is adopted immediately

- **GIVEN** a replica whose adopted partition is six members at rank 0 (cap 8 share is 2 slots)
- **WHEN** a refresh observes five members with this replica at rank 3 (churn removes members while adding lower-sorting instance ids)
- **AND** every configured cap's prospective share is no larger than the current share (for cap 8 the share drops from 2 to 1)
- **THEN** the replica adopts the five-member rank-3 partition on that refresh without holding the larger share for the stability window

#### Scenario: A changed pending target restarts the stability window

- **GIVEN** a replica deferring a share-growing partition
- **WHEN** a refresh observes a different pending partition before the stability window elapses
- **THEN** the stability window restarts for the new pending partition

#### Scenario: Idle peer capacity is borrowed for stream leases

- **GIVEN** a configured account stream cap of 8 partitioned across two replicas (share 4 each)
- **AND** the peer replica's fresh published in-flight count for account A is 0
- **AND** this replica holds 4 in-flight stream leases for account A
- **WHEN** a new stream lease for account A is requested on this replica
- **THEN** the borrow allowance is `floor((8 − 4) / 2) = 2`
- **AND** the lease is admitted instead of rejected with `account_stream_cap`

#### Scenario: Stale peer counts disable borrowing

- **GIVEN** two active replicas and a share-exhausted account on this replica
- **WHEN** the peer's published in-flight counts are missing or staler than the ring stale threshold
- **THEN** no borrow allowance is granted
- **AND** the static-share behavior applies unchanged

#### Scenario: Borrowing never applies to response-create leases

- **GIVEN** an account at this replica's response-create share
- **WHEN** a new response-create lease is requested
- **THEN** the lease is rejected with `account_response_create_cap` regardless of peer headroom
