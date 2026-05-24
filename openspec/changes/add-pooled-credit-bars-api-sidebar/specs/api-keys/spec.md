## ADDED Requirements

### Requirement: API key list includes pooled credit data

The `GET /api/api-keys/` list endpoint SHALL include per-key pooled credit data computed by aggregating upstream usage across the selectable accounts assigned to each key. When a key has no assigned accounts, the system SHALL pool across all selectable accounts.

Selectable accounts exclude accounts whose status is `paused` or `deactivated`, matching load-balancer routing eligibility.

The response SHALL include `pooled_remaining_percent_primary` (float or null), `pooled_remaining_percent_secondary` (float or null), and `pooled_capacity_credits_primary` (float, default 0.0) on each key object.

When `pooled_capacity_credits_primary` is 0.0 (e.g., all assigned accounts are free-tier), `pooled_remaining_percent_primary` SHALL be null.

#### Scenario: Scoped key pools assigned accounts only

- **WHEN** an API key has `assignedAccountIds` containing two accounts
- **AND** those accounts have usage data
- **THEN** `pooled_remaining_percent_primary` and `pooled_remaining_percent_secondary` reflect only those two accounts

#### Scenario: Unscoped key pools all accounts

- **WHEN** an API key has `assignedAccountIds` = []
- **THEN** pooled credit fields reflect all accounts in the system

#### Scenario: Free-tier accounts hide primary bar

- **WHEN** all assigned accounts have plan_type "free" (primary capacity = 0)
- **THEN** `pooled_capacity_credits_primary` = 0.0
- **AND** `pooled_remaining_percent_primary` = null

#### Scenario: Paused and deactivated accounts are excluded

- **WHEN** an API key has assigned accounts with active and paused statuses
- **THEN** pooled credit fields reflect only the active selectable accounts
