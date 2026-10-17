## MODIFIED Requirements

### Requirement: Unfiltered request-log filter options avoid full DISTINCT passes

When `GET /api/request-logs/options` is requested without user-supplied filters, each facet MUST traverse ordered distinct values with index probes rather than repeatedly scan the live request-log cohort or perform a full `DISTINCT` pass. A candidate value MUST be returned only when a row satisfies the endpoint's visibility and status predicates. Returned option sets, ordering, NULL handling, and soft-delete/status-facet semantics MUST match the unbounded `DISTINCT` results.

#### Scenario: Unfiltered facets return identical option sets via bounded probes

- **GIVEN** request logs spanning multiple accounts, models with and without reasoning effort, API keys, and statuses with and without error codes
- **WHEN** the options endpoint is called with no filters
- **THEN** each facet MUST advance through indexed distinct values rather than a full `DISTINCT` pass
- **AND** the response MUST equal the legacy `DISTINCT` results, including `(value, NULL)` pairs and ordering

#### Scenario: SQLite facet traversal does not require planner statistics

- **GIVEN** SQLite request logs with many repeated visible rows, the standard facet indexes, and no planner statistics
- **WHEN** the unfiltered options endpoint is called
- **THEN** facet traversal MUST use the facet's ordered values and equality-constrained eligibility checks
- **AND** adding duplicate visible rows MUST NOT cause each recursive step to rescan the entire live-row cohort

#### Scenario: Soft-deleted rows stay excluded from skip-scanned facets

- **GIVEN** request-log values present only in rows with `deleted_at` set or an unsupported status
- **WHEN** the options endpoint is called without filters
- **THEN** those values MUST NOT be returned
- **AND** a value shared by visible and invisible rows MUST be returned exactly once when a visible row qualifies

#### Scenario: Filtered requests keep bounded DISTINCT semantics

- **WHEN** the options endpoint receives user filters such as time bounds, account, API-key, model, or reasoning-effort constraints
- **THEN** the facets MUST apply those filters with unchanged semantics and results
