## Summary

Request-log `transport` alone is not enough to debug compact regressions because both standard Responses and compact currently land as `HTTP` rows. We need one additional semantic discriminator plus one safe correlation key that lets operators match a compact row with the next follow-up response row on the same Codex thread.

## Decisions

### 1. Persist semantic request kind

We persist a new `request_kind` column on `request_logs` and expose it through `/api/request-logs`.

Chosen values:
- `responses`
- `compact`
- `transcription`

This keeps the contract explicit without overloading `transport`.

### 2. Persist only hashed session correlation

We persist `session_id_hash` when an inbound request carries `session_id`, but we never store the raw `session_id`.

Why:
- operators only need correlation, not the original secret-ish identifier
- the same hash is stable across compact and follow-up response calls
- the request-log table remains safe to expose in the dashboard

### 3. Reuse existing proxy ownership

`ProxyService` already owns request-log writes for HTTP Responses, compact, websocket Responses, and transcription flows. We keep the new fields in that layer instead of creating a separate request-log enrichment pipeline.

### 4. Show correlation directly in the recent requests table and filters

The dashboard recent requests table renders:
- a route badge from `requestKind`
- a small monospace `sessionIdHash` when present
- filter facets for `requestKind` and `transport`

This keeps the debugging signal where operators already inspect compact incidents, without introducing a separate drill-down screen.

## Rejected alternatives

### Reuse `transport` for semantic meaning

Rejected because `HTTP` does not distinguish `/responses` from `/responses/compact`.

### Store raw `session_id`

Rejected because the UI only needs a correlation key, not the original identifier.

### Add a separate correlation table

Rejected because request-log persistence already sits at the right boundary and the additional fields are lightweight.
