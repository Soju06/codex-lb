## ADDED Requirements

### Requirement: Upstream filters use exact listing counts with isolated cache identity

When a request-log listing carries an `upstream` filter, its `total` MUST be counted from request-log rows rather than from the demand rollup, because the demand rollup has no model-source dimension. The request-log listing-count cache signature MUST include the normalized upstream selection in addition to every existing filter dimension, so listings that differ only by upstream selection MUST NOT reuse one another's cached count. Listings without an `upstream` filter MUST keep their existing count path.

#### Scenario: Upstream-filtered total is exact when demand rollups exist
- **GIVEN** folded demand-rollup rows and raw request logs from two different upstreams
- **WHEN** the listing is requested with `upstream=src_a`
- **THEN** `total` equals the number of visible `src_a` rows

#### Scenario: Different upstream selections have isolated cached totals
- **GIVEN** two listing requests differ only by upstream selection
- **WHEN** their listing counts are served through the cache
- **THEN** each request uses its own cache entry and filtered total

### Requirement: Unfiltered upstream facet uses indexed probes

When `GET /api/request-logs/options` is requested without user-supplied filters, the upstream facet MUST traverse distinct non-NULL `model_source_id` values with index probes rather than a full `DISTINCT` pass, and MUST detect `subscription_pool` with one bounded probe for a visible row whose `model_source_id` is NULL. The returned upstream set MUST equal the set produced by the filtered `DISTINCT` path over the same visible rows.

#### Scenario: Unfiltered upstream facet matches the DISTINCT path
- **GIVEN** visible request logs with no model source and with several model sources, plus soft-deleted rows from another source
- **WHEN** the options endpoint is called with no filters and with a time bound that covers every row
- **THEN** both responses return the same `upstreams`
- **AND** the unfiltered call issues no `DISTINCT` statement
