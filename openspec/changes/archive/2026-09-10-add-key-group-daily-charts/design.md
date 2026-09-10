## Context

The group endpoint already resolves current membership and merges hourly rollups with complementary raw windows. See proposal.md for motivation. Existing totals and privacy contracts remain authoritative.

## Goals / Non-Goals

Compute daily series and totals from one aggregation path without per-key or per-day query fan-out. Do not introduce a separate endpoint, storage table, group policy, arbitrary date range, or timezone setting.

## Decisions

- Group each member's usage into UTC calendar days inside the existing rolling window. A non-midnight window intersects 31 dates, with partial edges; a midnight window intersects 30. Explicitly label this in the UI rather than silently changing the totals window or assuming a browser timezone.
- Aggregate raw rows by key and date in SQL and merge hourly rollups into the same typed daily accumulators. Derive top-level totals from these accumulators; round daily cost to six decimals and sum those values for the published total so display data reconciles.
- Return a dense daily series for each member, including unused days and members. No internal member ID is required; chart series keys are response-local indices.
- Load a dedicated chart component lazily inside Group keys, using the existing Recharts dependency. A token/cost toggle and member checkboxes act on already-loaded data. Supply a collapsible date-by-member table for precise values on touch devices and assistive technology.

## Risks / Trade-offs

- UTC may differ from the viewer's local day → label the timezone and boundary behavior clearly; preserve date-only labels without browser-timezone conversion.
- Large groups can make overlapping lines hard to compare → member visibility controls, scrollable legend/table, and tooltips showing names and prefixes.
- Raw retention may remove a partial hourly edge → preserve and document the established rollup limitation; daily buckets do not fabricate unavailable data.

## Migration Plan

No database migration. Update API and SPA together through the normal release workflow. Tests verify additive serialization and existing group behavior.
