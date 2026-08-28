# proxy-runtime-observability

## ADDED Requirements

### Requirement: Cap partition replica count is observable

The service MUST expose a Prometheus gauge named `codex_lb_cap_partition_replicas` whose value equals the live replica count currently used for account cap partitioning, and it MUST log adopted partition rebalances at info level with the old count, the new count, and this replica's rank. The gauge and log MUST NOT include account ids, instance secrets, or request payload content.

#### Scenario: Partition rebalance updates the gauge

- **GIVEN** a replica whose adopted partition has replica count 1
- **WHEN** a partition refresh observes and adopts two active members
- **THEN** `codex_lb_cap_partition_replicas` reports 2
- **AND** an info-level log records the rebalance from count 1 to count 2 with the replica's rank
