# Change: Optimize dashboard request log pagination

## Problem

Production dashboard request-log listing on 10.0.0.113 is slow because the repository combines pagination with `count(*) OVER()` in the same query. PostgreSQL must materialize the full filtered request-log result before returning the first page. In a measured production case, `GET /api/request-logs?limit=25&offset=0` materialized roughly 1.1M rows, spilled hundreds of MB to temp storage, and made the service method take double-digit seconds.

The dashboard still needs stable pagination metadata, but the hot page fetch must not be coupled to a full-result window aggregate.

## Solution

Split request-log pagination into two query shapes:

1. Page query: fetch only the requested page ordered latest-first
2. Count query: compute exact total separately for the same filters

This preserves the existing API contract (`requests`, `total`, `hasMore`) while letting PostgreSQL use the existing latest-first index for the page query instead of materializing the entire filtered result before applying `LIMIT`.

## Changes

- Add a query-caching/query-shape requirement for dashboard request-log pagination
- Update `RequestLogsRepository.list_recent()` to avoid `count(*) OVER()` on the paginated row query
- Add regression coverage that captures emitted SQL and rejects window-count pagination
- Document the broader source-structured dashboard optimization direction for projections/filter facets

## Out of scope

- Removing exact `total` from the public API response
- Adding dashboard projection snapshot tables in this change
- Reworking filter-option facets into cached/source-structured summaries in this change
- Production rollout/deploy in this PR
