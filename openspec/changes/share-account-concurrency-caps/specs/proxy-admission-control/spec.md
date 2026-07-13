# proxy-admission-control

## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits, and the configured limits MUST be cluster-wide per-account targets rather than per-replica allowances. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value. When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. Hard-continuity work MAY fail closed when the required owner account is saturated.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a soft-affinity `/v1/responses` request is routed
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

#### Scenario: Two replicas do not double an account cap

- **GIVEN** two replicas are active in the bridge ring
- **AND** the configured account stream cap is 8
- **WHEN** both replicas admit streams for the same account until each rejects
- **THEN** the aggregate admitted streams for that account do not exceed 8

## ADDED Requirements

### Requirement: Account concurrency caps are partitioned across live replicas

Each replica MUST derive its local share of every configured account concurrency cap deterministically from the sorted active bridge-ring member list: with `R` active members and this replica at rank `k` in instance-id order, the share MUST be `floor(cap / R)` plus one extra slot when `k < cap mod R`, floored at one slot so an account never becomes unroutable on a replica; a nonpositive configured cap MUST remain unlimited on every replica. Partition derivation MUST NOT add database reads to the request or admission path; it MUST refresh from bridge-ring registration and heartbeat ticks, and the observing replica MUST count itself even when its own ring row is missing or stale. Membership changes that cannot grow this replica's share of any cap MUST be adopted on the next refresh; membership changes that could grow this replica's share MUST NOT be adopted until that exact pending partition (member count and rank) has been observed continuously for the configured stability window. Whether a change could grow the share MUST be decided by comparing the prospective share against the current share for each configured cap (the response-create and stream limits actually in effect — the dashboard-configured overrides when present and otherwise the startup defaults, i.e. the same effective caps the admission path partitions, never the startup defaults when a dashboard override differs) using the same share formula the admission path enforces, and MUST NOT be decided from the direction of the member count or the rank alone: neither direction determines growth, because a member-count decrease can be outweighed by a rank increase and a rank decrease by a large enough member-count increase. A change MUST be deferred only when some configured cap's prospective share is strictly greater than its current share; a change whose every configured cap's prospective share is less than or equal to its current share MUST be adopted on the next refresh, whether the member count or rank rose or fell (for example a member-count decrease paired with a rank increase that reduces this replica's configured share, as when churn removes members while adding lower-sorting instance ids, MUST be adopted immediately rather than held). The stability window (`proxy_account_cap_partition_scale_down_seconds`, default 60 seconds, minimum 30) applies to deferred share-growing changes only; a change of the pending partition, including a rank change at an unchanged count, MUST restart the window. A failed membership read MUST retain the last adopted partition; while a share-growing change is pending, a failed read MUST also restart the stability window so the observation gap does not count toward the continuous-stable requirement. Setting `proxy_account_caps_scope` to `replica` MUST restore per-replica cap semantics, and a replica that observes no other active member MUST use the full configured caps.

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

- **GIVEN** a replica that has held a share-growing pending partition for part of the stability window
- **WHEN** a refresh observes a different share-growing partition, such as an earlier rank at the same member count
- **THEN** the stability window restarts for the new pending partition
- **AND** the new partition is adopted only after it has been observed continuously for the full window

#### Scenario: Hysteresis gates on the dashboard-configured effective caps

- **GIVEN** a startup stream cap of 8 and a dashboard-configured stream cap of 19
- **AND** a replica whose adopted partition is five members at rank 0 (cap-19 share is 4 slots)
- **WHEN** a refresh observes four members with this replica at rank 2 (no growth for cap 8, but the cap-19 share grows from 4 to 5)
- **THEN** the replica holds its previous partition until the change has been observed continuously for the full stability window
- **AND** the decision uses the dashboard-configured caps, not the startup defaults, so it agrees with the caps the admission path partitions

#### Scenario: Failed membership read retains the partition

- **GIVEN** a replica with an adopted two-way partition
- **WHEN** a partition refresh fails to read ring membership
- **THEN** the replica keeps the two-way partition
- **AND** it does not fall open to the full configured caps

#### Scenario: Failed membership read restarts a pending share-increase window

- **GIVEN** a replica with a share-growing partition pending part-way through the stability window
- **WHEN** a partition refresh fails to read ring membership
- **THEN** the pending stability window is restarted
- **AND** the share-growing partition is adopted only after being observed continuously for the full window from the next successful read

#### Scenario: Replica scope restores legacy semantics

- **GIVEN** `proxy_account_caps_scope` is `replica`
- **AND** two active replicas
- **WHEN** a replica computes its effective account caps
- **THEN** it uses the full configured caps without partitioning

#### Scenario: Partitioned cap rejection states the replica share

- **GIVEN** two active replicas partitioning a configured stream cap of 8
- **WHEN** a request is rejected because the replica's stream share is exhausted
- **THEN** the local overload message states the replica's share, the configured per-account limit, and the replica count
- **AND** the stable reason remains `account_stream_cap`
