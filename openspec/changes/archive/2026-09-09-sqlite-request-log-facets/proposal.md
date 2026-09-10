## Why

The dashboard's unfiltered request-log options endpoint takes 4.7–6.2 seconds on a populated SQLite database. Its recursive minimum queries choose the soft-delete index and repeatedly scan the live-row cohort instead of advancing through each facet's ordered index.

## What Changes

- Separate SQLite facet-value traversal from row-visibility checks so recursive probes can advance through the facet index even without planner statistics.
- Preserve option contents, ordering, NULL handling, soft deletion, and status semantics.
- Add an endpoint regression that measures SQLite work on synthetic data rather than relying on wall-clock thresholds.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: unfiltered SQLite facet probes must avoid repeatedly scanning the live-row cohort when planner statistics are absent.

## Impact

The request-log repository and options endpoint tests change. No new settings, indexes, migrations, dependencies, or frontend controls are required. PostgreSQL and filtered option queries retain their existing query paths. This addresses SQLite query planning separately from the live-row indexes proposed in PR #2246.
