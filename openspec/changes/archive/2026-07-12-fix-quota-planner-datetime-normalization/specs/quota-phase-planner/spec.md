## ADDED Requirements

### Requirement: Planner repository datetime boundaries are UTC-normalized

Quota phase planner repository methods MUST normalize timezone-aware datetime
inputs to naive UTC before binding those values into database comparisons or
persisted planner observation timestamps.

#### Scenario: Aware datetimes are accepted at repository boundaries

- **GIVEN** quota planner repository calls receive timezone-aware datetime
  values for warmup decision queries, demand aggregation, or quota window
  observations
- **WHEN** those calls bind the values into database statements
- **THEN** the bound values use naive UTC timestamps
- **AND** the queries return rows that match the equivalent UTC instant
