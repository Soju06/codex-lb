## ADDED Requirements

### Requirement: Bounded Desktop pool refresh wait

The Desktop quota request MUST limit its aggregate pool-refresh wait to five seconds. If that wait expires, it MUST evaluate the latest persisted observations using the same freshness and completeness requirements. It MUST NOT invent capacity or accept stale observations because refresh timed out. Caller cancellation MUST still propagate, and shared refresh work MUST retain its existing resource ownership.

#### Scenario: Refresh times out with complete fresh observations

- **WHEN** the pool-refresh wait expires and persisted observations remain complete and fresh
- **THEN** the route returns the strict pooled projection from those observations

#### Scenario: Refresh times out with stale observations

- **WHEN** the pool-refresh wait expires and any required observation is stale
- **THEN** the route returns `pooled_usage_unavailable` rather than reporting capacity
