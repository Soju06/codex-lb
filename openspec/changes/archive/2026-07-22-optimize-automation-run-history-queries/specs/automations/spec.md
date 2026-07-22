## ADDED Requirements

### Requirement: Grouped run-history selection is bounded and snapshot-consistent

The grouped automation run-history repository MUST preserve existing candidate-cycle filtering, representative-run selection, cycle ordering, effective-status calculation, and filter-option semantics on SQLite and PostgreSQL while bounding repeated database work. For a non-empty grouped page, page selection, representative-run loading, and exact total calculation MUST use one database statement so rows and total come from one statement snapshot. When an offset is beyond the final row, the repository MAY execute one additional count statement to preserve the exact-total contract. Account and model filter-option facets MUST be loaded with no more than one database statement; status and trigger options MUST continue to come from their canonical complete enums.

#### Scenario: Common page skips dynamic status aggregation

- **WHEN** grouped run history is requested without an effective-status filter
- **THEN** candidate cycles and their representatives are selected without computing current account eligibility or effective cycle status
- **AND** search, account, model, trigger, and job filters retain their existing candidate-cycle semantics
- **AND** manual and scheduled cycles retain their existing ordering rules

#### Scenario: Status-filtered page keeps dynamic cycle semantics

- **GIVEN** cycle status depends on visible accounts, current eligibility, paused-account policy, pending-window time, completed outcomes, or hidden manual placeholders
- **WHEN** grouped run history is filtered by effective status
- **THEN** the page uses the full dynamic status calculation over each selected candidate cycle
- **AND** the returned cycles match the same effective statuses as before the query refactor

#### Scenario: Non-empty page rows and total share one snapshot

- **WHEN** a grouped run-history offset returns at least one cycle
- **THEN** representative run rows and the exact matching total are returned by one repository database statement
- **AND** the total describes the same statement snapshot as the returned rows

#### Scenario: Offset beyond the final page keeps exact total

- **GIVEN** matching cycle history exists
- **WHEN** the requested offset is beyond the final matching cycle
- **THEN** the response contains no items
- **AND** it reports the exact non-zero total using at most one bounded count fallback

#### Scenario: Representative and ordering semantics survive filtering

- **GIVEN** a cycle contains multiple attempts and only a subset matches the request filters
- **WHEN** the grouped page is selected
- **THEN** the representative is the latest matching run by `started_at` and `id`
- **AND** the cycle order is derived from the complete cycle's established manual or scheduled start rule
- **AND** model filtering uses the execution snapshot rather than a job's later model value

#### Scenario: Filter options use one facet query

- **WHEN** automation run filter options are requested with any supported filter combination
- **THEN** account and model facets are loaded in no more than one repository database statement
- **AND** status and trigger choices remain the canonical complete sets even when stored history is sparse
- **AND** without an effective-status filter, account and model facets are derived directly from matching run rows
- **AND** with an effective-status filter, observed runs or snapshot membership may qualify a cycle before account and model facets expand over all observed run rows in status-matching cycles
- **AND** snapshot-only account IDs are not synthesized into the returned account facet
