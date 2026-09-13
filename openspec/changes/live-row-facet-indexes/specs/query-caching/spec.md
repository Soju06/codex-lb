## MODIFIED Requirements

### Requirement: Unfiltered request-log filter options avoid full DISTINCT passes

When `GET /api/request-logs/options` is requested without user-supplied filters, each facet (account ids, model/reasoning-effort pairs, api-key ids, status/error-code pairs) MUST be computed with loose-index-scan probes bounded by the facet's distinct-value count, not by the size of `request_logs`. Each successor probe over api-key ids, model/reasoning-effort pairs and status/error-code pairs MUST be served by an index whose predicate excludes soft-deleted rows (`deleted_at IS NULL`), so probe cost is independent of the number of soft-deleted rows sharing a value. The returned option sets, their ordering, and the soft-delete/status-facet semantics MUST be identical to the unbounded `DISTINCT` results.

#### Scenario: Unfiltered facets return identical option sets via bounded probes

- **GIVEN** request logs spanning multiple accounts, models with and without reasoning effort, api keys, and statuses with and without error codes
- **WHEN** the options endpoint is called with no filters
- **THEN** each facet MUST be produced by per-distinct-value index probes (recursive skip scan) rather than a full `DISTINCT` pass
- **AND** the response MUST equal the legacy `DISTINCT` results, including `(value, NULL)` pairs and ordering

#### Scenario: Soft-deleted rows stay excluded from skip-scanned facets

- **GIVEN** request-log rows with `deleted_at` set
- **WHEN** the options endpoint is called with no filters
- **THEN** values appearing only on soft-deleted rows MUST NOT appear in any facet

#### Scenario: Soft-deleted cohorts do not lengthen facet probes

- **GIVEN** live request-log rows spread over several models, api keys and statuses, soft-deleted rows sharing those values, and a large soft-deleted cohort on a model, api-key id and error code that no live row carries
- **WHEN** the options endpoint is called with no filters on PostgreSQL and each issued `facet_skip` statement for the model, api-key and status facets is explained
- **THEN** every `min()` seed and successor probe MUST be served by the facet's live-row partial index (`idx_logs_live_model_effort`, `idx_logs_live_api_key`, `idx_logs_live_status_error`), remove no rows by filter and read a single index entry per probe
- **AND** every `(value, NULL)` existence probe MUST be served by an index whose predicate excludes soft-deleted rows
- **AND** the options response MUST exclude the soft-deleted cohort's values

#### Scenario: Filtered requests keep bounded DISTINCT semantics

- **WHEN** the options endpoint is called with any user filter (`since`, `until`, account, api-key, model, or reasoning-effort constraints)
- **THEN** the facets MUST apply those filters with unchanged semantics and results
