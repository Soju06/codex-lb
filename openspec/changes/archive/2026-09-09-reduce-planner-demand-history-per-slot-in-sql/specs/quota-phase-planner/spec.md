## ADDED Requirements

### Requirement: Planner demand history is reduced per slot inside the database

The quota planner SHALL obtain its demand-forecast history as demand units
already summed per `(slot_epoch, request_kind)` by the database, not as one
row per legacy demand grain (`slot`, `account`, `api_key`, `model`,
`reasoning_effort`, `request_kind`, `status`). The per-row demand-unit formula
(the maximum of token, cost, and request units with non-negative clamps) MUST
be applied per legacy-grain row before the per-slot sum, so the reduced result
equals the Python reduction of the exact-grain rows for any watermark
position. The folded rollup segment and the un-folded raw tail MUST keep the
existing watermark-consistent partition. A planner tick MUST log its duration
so a slow tick is attributable from logs alone.

#### Scenario: A tick over a long history returns a bounded row set

- **GIVEN** 28 days of demand history at the legacy grain across many
  accounts, keys, models, and efforts
- **WHEN** the planner tick loads demand history
- **THEN** the number of rows materialized in the process is bounded by slots
  times request kinds, independent of how many accounts, keys, models, or
  efforts produced traffic

#### Scenario: Reduced history equals the exact-grain reduction

- **GIVEN** the same window read as exact-grain bins and as per-slot units
- **WHEN** both are reduced to demand units per slot
- **THEN** the per-slot totals agree within floating-point tolerance for the
  epoch watermark, a mid-history watermark, the full target watermark, and the
  raw-degraded state after the operator escape hatch

#### Scenario: Tick duration is observable

- **GIVEN** a replica that ran a planner tick as leader
- **WHEN** the tick completes
- **THEN** the replica logs the tick duration in milliseconds
