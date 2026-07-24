## Why

The dashboard request-log view exposes one request at a time. When an operator
needs an aggregate picture of a whole Codex/OpenCode conversation — which
accounts it touched, how long it ran, which models and efforts it used, and how
many tokens it burned — there is no first-class view. The operator is forced to
filter request logs by conversation ID and mentally sum the rows, which does not
scale across long multi-account conversations. A paginated, aggregate
conversation view is needed.

## What Changes

- Add an authenticated dashboard API `GET /api/conversations` returning paginated
  conversation aggregates derived from `request_logs`, with only `limit`,
  `offset`, and `search` parameters.
- The list response rows contain exactly `conversationId`, `lastRequest`,
  `representativeAccount`, `remainingAccountCount`, `apiKeyId`,
  `apiKeyName`, `representativeModel`, `remainingModelCount`,
  `totalTokens`, `cachedInputTokens`, and `totalCostUsd`. Model
  representatives and remaining counts are grouped by distinct model, not by
  reasoning effort.
- Add an authenticated dashboard API
  `GET /api/conversations/{conversation_id}` returning conversation detail
  statistics: start/latest time, account count, total elapsed time (cumulative
  per-request latency), dominant user-agent group, and a model-plus-effort table
  with the operator-required columns.
- Add a switchable dashboard view: Request Logs remains the default, and the
  original-styled section title is the single accessible selector for switching
  to Conversations. The Conversations view has no filter input above its list.
  Request Logs and Conversations retain separate URL-backed query state;
  switching views does not reinterpret or clear the other view's state.
- Present Last request before Conversation using the request-log Time column's
  two-line format, resolve account IDs to human-readable dashboard account
  labels, and omit the conversation-ID copy action from the detail dialog.
- Add detail statistics whose displayed model-plus-effort table has exactly these
  columns: Model (effort), Reqs, Total elapsed, Total input (with total cache as
  a subordinate/parenthetical value), Total output, and Total cost. It defaults
  to `reqs DESC` from the API and supports client-side sorting for every
  displayed column with no sort query parameter.
- Add backend and frontend regression coverage for the new external contracts,
  including case-insensitive API list search, independent retained URL query
  state, the title-styled selector, exact list rendering, detail
  loading/error/retry behavior, nullable aggregate fallbacks, and the existing
  empty state.
- No migration or schema change is part of this work; existing indexes are
  reused.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: the dashboard gains authenticated conversation list
  and detail APIs plus a switchable Conversations view. The change-level delta
  is owned by frontend architecture; the proxy observability main spec is not
  synchronized by this change.

## Impact

- **Backend**: `app/modules/request_logs/` gains conversation list/detail service
  and repository aggregation methods and new dashboard API routes; reuses the
  existing request-log persistence model and the existing request-log search and
  `warmup`/`limit_warmup` exclusion clauses.
- **Frontend**: the dashboard gains a title-styled Radix Request Logs /
  Conversations selector, a Conversations list without a filter input, and a
  sortable detail experience.
- **Tests**: backend integration tests for the two APIs (stable pagination order,
  non-empty conversation IDs, `warmup`/`limit_warmup` and soft-delete exclusion, search-before-
  grouping with whole-conversation aggregation, representative selection,
  cumulative elapsed time, token clamping with the reasoning-token fallback, the
  exact wire columns, the default `reqs DESC` order, and encoded-blank/unknown
  conversation IDs) and frontend tests for the selector default, list columns,
  and client-side detail sorting.
- **Schema and dependencies**: no migration, dependency, setting, README, or
  changelog change.

## Non-goals

- Conversation inference or backfill for historical request logs that lack a
  `conversation_id` are out of scope; only rows that already carry a non-empty
  `conversation_id` are aggregated.
- No changes to proxy routing, request-log capture, or the conversation-ID
  detection rules.
- No date/timeframe controls or `since`/`until` list parameters.
