## ADDED Requirements

### Requirement: Dashboard reads avoid hot-path full-history recomputation

The system SHALL keep dashboard hot-path database reads bounded by the data needed for the requested response whenever the existing API contract allows it. Dashboard query shapes MUST NOT combine a limited page fetch with an unbounded window aggregate that forces the database to materialize the entire filtered result before returning the page.

`GET /api/request-logs` MUST fetch request-log rows using a latest-first limited page query. If the response includes exact total metadata, the exact count MUST be computed using a separate count query or an equivalent cached/source-structured summary, not by adding `count(*) OVER()` to the paginated row query.

#### Scenario: Request-log page query does not materialize the full filtered result

- **GIVEN** the request-log table contains many rows matching the active filters
- **WHEN** the dashboard requests `GET /api/request-logs?limit=25&offset=0`
- **THEN** the row-fetch query returns only the requested page ordered by newest request first
- **AND** the row-fetch query does not include `count(*) OVER()` or an equivalent unbounded window aggregate
- **AND** the response still includes correct `total` and `hasMore` metadata

#### Scenario: Source-structured summaries remain available for broader dashboard optimization

- **GIVEN** a dashboard read repeatedly aggregates large raw histories such as request logs or usage history
- **WHEN** the aggregation cost dominates dashboard latency
- **THEN** the system MAY move that read to a cached, incremental, or source-structured summary so the dashboard does not repeatedly scan raw history on every poll
- **AND** the summary contract MUST preserve the externally visible dashboard semantics
