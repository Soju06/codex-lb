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

Each replica MUST derive its local share of every configured account concurrency cap deterministically from the sorted active bridge-ring member list: with `R` active members and this replica at rank `k` in instance-id order, the share MUST be `floor(cap / R)` plus one extra slot when `k < cap mod R`, floored at one slot so an account never becomes unroutable on a replica; a nonpositive configured cap MUST remain unlimited on every replica. Partition derivation MUST NOT add database reads to the request or admission path; it MUST refresh from bridge-ring registration and heartbeat ticks, and the observing replica MUST count itself even when its own ring row is missing or stale. When the active member count increases, the smaller shares MUST be adopted on the next refresh; when it decreases, the larger shares MUST NOT be adopted until the lower count has been observed continuously for the configured stability window (`proxy_account_cap_partition_scale_down_seconds`, default 60 seconds, minimum 30). A failed membership read MUST retain the last adopted partition. Setting `proxy_account_caps_scope` to `replica` MUST restore per-replica cap semantics, and a replica that observes no other active member MUST use the full configured caps.

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

#### Scenario: Failed membership read retains the partition

- **GIVEN** a replica with an adopted two-way partition
- **WHEN** a partition refresh fails to read ring membership
- **THEN** the replica keeps the two-way partition
- **AND** it does not fall open to the full configured caps

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
