# Request Log User-Agent Tracking Design

## Purpose

Persist prompt-client user-agent metadata on `request_logs` so operators can inspect which client family produced a request across both HTTP and WebSocket proxy flows.

## Scope

This change covers:

- request-log schema updates for full user-agent storage and grouped lookup
- proxy request-log capture for HTTP and WebSocket routes
- dashboard request-log API exposure of the new fields
- Request Details dialog rendering for the full user-agent string

This change does not add request-log filtering or table-column rendering for user-agent values.

## Data Model

Add two nullable columns to `request_logs`:

- `useragent`: the full inbound `User-Agent` header after trimming outer whitespace
- `useragent_group`: the derived client group used for indexed lookup

Add one index:

- `ix_request_logs_useragent_group` on `request_logs.useragent_group`

Both fields remain nullable so legacy rows and requests without a user-agent continue to load without backfill requirements.

## Extraction Rules

Capture the inbound `User-Agent` once before request-log persistence and pass the resolved values through the existing request-log write path.

Resolution rules:

1. Missing header -> `useragent = NULL`, `useragent_group = NULL`
2. Header present but blank after trimming -> `useragent = NULL`, `useragent_group = NULL`
3. Non-empty header ->
   - `useragent` = full trimmed string
   - `useragent_group` = the first whitespace-delimited token, truncated at the first `/`

Example:

- input: `opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- stored `useragent`: `opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- stored `useragent_group`: `opencode`

If the first token lacks a `/`, store that token as the group.

## Capture Location

Use `ProxyService` as the canonical capture point because both HTTP and WebSocket request-log writes already converge there.

Flow:

- HTTP/SSE route handlers forward headers into `ProxyService`
- WebSocket handlers pass handshake headers into `ProxyService`
- `ProxyService` resolves `useragent` and `useragent_group`
- `_write_request_log` and `_persist_request_log` pass both values to `RequestLogsRepository.add_log`
- `RequestLogsRepository.add_log` persists both columns on `RequestLog`

This avoids recomputing the group at read time and keeps behavior consistent between transports.

## API And UI

Extend the request-log API payload with nullable fields:

- `useragent`
- `useragentGroup`

The mapper should surface `null` for rows without stored values.

Update the dashboard Request Details dialog to render a `User Agent` field directly below the existing `Transport | Time | Error Code` row.

Rendering rules:

- show the full `useragent` string when present
- show `—` when `useragent` is `null`
- allow copying the full value using the existing detail-field copy affordance

No request-log table-column or filter changes are included in this scope.

## Testing Strategy

Use TDD for each layer.

Backend coverage:

- repository test for persisting full `useragent` and derived `useragent_group`
- unit test for extraction rules, including missing and blank headers
- proxy service coverage proving HTTP request-log writes carry the values
- proxy service coverage proving WebSocket request-log writes carry the values

Frontend coverage:

- schema tests for nullable `useragent` and `useragentGroup`
- Request Details component test showing the full user-agent string
- Request Details component test showing `—` for null values

Migration coverage should follow the existing Alembic revision pattern and preserve a single valid upgrade path from the current head.

## Constraints And Risks

- The change must not break legacy request-log rows that lack the new columns until migrated.
- HTTP and WebSocket flows must produce the same normalization rules.
- The indexed group must remain low-cardinality enough for operator search, so only the first product token is stored.
- No fallback placeholder such as `unknown` should be persisted; absent or blank headers remain `NULL`.

## Parallel Execution Shape

After approval, implementation can split into three workstreams:

1. OpenSpec artifacts: proposal, delta specs, tasks
2. Backend: migration, model/repository/proxy updates, backend tests
3. Frontend: API schema updates, Request Details UI, frontend tests

The frontend workstream depends only on the agreed field names and nullability, so it can proceed in parallel with backend persistence once the spec artifacts are written.
