# query-caching Delta

## ADDED Requirements

### Requirement: Unfiltered request-log filter options avoid full DISTINCT passes

When `GET /api/request-logs/options` is requested without user-supplied filters, each facet (account ids, model/reasoning-effort pairs, api-key ids, status/error-code pairs) MUST be computed with loose-index-scan probes bounded by the facet's distinct-value count, not by the size of `request_logs`. The returned option sets, their ordering, and the soft-delete/status-facet semantics MUST be identical to the unbounded `DISTINCT` results.

#### Scenario: Unfiltered facets return identical option sets via bounded probes

- **GIVEN** request logs spanning multiple accounts, models with and without reasoning effort, api keys, and statuses with and without error codes
- **WHEN** the options endpoint is called with no filters
- **THEN** each facet MUST be produced by per-distinct-value index probes (recursive skip scan) rather than a full `DISTINCT` pass
- **AND** the response MUST equal the legacy `DISTINCT` results, including `(value, NULL)` pairs and ordering

#### Scenario: Soft-deleted rows stay excluded from skip-scanned facets

- **GIVEN** request-log rows with `deleted_at` set
- **WHEN** the options endpoint is called with no filters
- **THEN** values appearing only on soft-deleted rows MUST NOT appear in any facet

#### Scenario: Filtered requests keep bounded DISTINCT semantics

- **WHEN** the options endpoint is called with any user filter (`since`, `until`, account, api-key, model, or reasoning-effort constraints)
- **THEN** the facets MUST apply those filters with unchanged semantics and results
