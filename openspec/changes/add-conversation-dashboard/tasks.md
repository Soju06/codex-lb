## 1. OpenSpec Change Contract

- [x] 1.1 Author the approved `proposal.md`, `design.md`, `tasks.md`, and
  `frontend-architecture` delta `spec.md`; keep delta ownership out of
  `proxy-runtime-observability`.
- [x] 1.2 Validate the OpenSpec main specs and this change in strict mode after
  implementation; do not synchronize the main capability specs as part of this
  change.

## 2. Backend Conversation Listing API

- [x] 2.1 Define one shared eligible-row scope for list and detail aggregation:
  normalized non-blank conversation IDs, rows whose request kind is neither
  `warmup` nor `limit_warmup`, and `deleted_at IS NULL`.
- [x] 2.2 Add repository aggregation for `GET /api/conversations` where
  case-insensitive search matches the normalized conversation ID or any eligible
  row's user-agent family, selects whole matching conversations, and aggregates
  all eligible rows in each selected conversation. The comparison MUST be
  case-insensitive for both normalized IDs and user-agent families.
- [x] 2.3 Return exactly `conversationId`, `lastRequest`,
  `representativeAccount`, `remainingAccountCount`, `apiKeyId`,
  `apiKeyName`, `representativeModel`, `remainingModelCount`,
  `totalTokens`, `cachedInputTokens`, and `totalCostUsd` per row. Group
  list model values by distinct model across all reasoning efforts. Order the
  representative model by `request_count DESC, latest DESC, model lexical ASC`
  and use deterministic representative ordering and safe nullable/multiple
  API-key handling.
- [x] 2.4 Add the authenticated list route with only `limit`, `offset`, and
  `search`, stable `lastRequest DESC, normalized conversation ID ASC` order,
  and pagination metadata.
- [x] 2.5 Add backend integration tests for pagination, stable ordering,
  blank-ID exclusion, `warmup`/`limit_warmup`/soft-delete exclusion, search
  whole-conversation aggregation, case-insensitive normalized-ID and
  user-agent-family search, distinct-model representatives and `+ N more`
  counts across reasoning efforts, token/cost totals, nullable and multiple API
  keys, and empty results.

## 3. Backend Conversation Detail API

- [x] 3.1 Add detail aggregation using the shared eligible-row scope (including
  exclusion of `warmup` and `limit_warmup` request kinds) and compute conversation
  ID, start/latest, account count, dominant user-agent family, and cumulative
  `SUM(COALESCE(latency_ms, 0))`.
- [x] 3.2 Return model/effort rows with exactly `modelEffort`, `reqs`,
  `totalElapsedTime`, `totalInputTokens`, `cachedInputTokens`,
  `totalOutputTokens`, and `totalCostUsd`; preserve output fallback and
  cached-input clamping.
- [x] 3.3 Add the authenticated detail route with API ordering
  `reqs DESC, latest request DESC, lexical ASC` and no sort query parameter. An
  encoded blank path such as `/api/conversations/%20` returns the standard 404;
  an unknown non-empty ID also returns the standard 404.
- [x] 3.4 Add backend integration tests for cumulative elapsed time, exact row
  columns, ordering and no-API-sort behavior, dominant user-agent family,
  `warmup`/`limit_warmup`/soft-delete exclusion, encoded blank ID, and unknown
  non-empty ID.

## 4. Frontend Conversations View

- [x] 4.1 Add a styled Radix-style selector using `ChevronDown`, with Request
  Logs as the default and URL-backed `view=conversations` selection.
- [x] 4.2 Add the Conversations view; query the list endpoint using `limit`,
  `offset`, and `search`, and retain established loading, error, empty, and
  pagination behavior without date/timeframe controls. Keep separate URL-backed
  query state for Request Logs and Conversations, so switching views neither
  reinterprets nor clears the other view's state.
- [x] 4.3 Render the conversation list columns Last request, Conversation,
  Accounts,
  API key, Models, Tokens, Cost, and Details. Render account/model remainder as a
  smaller muted `+ N more` secondary line, put cached tokens on a subordinate
  line below total tokens, use the existing Details button, and display only the
  dashboard-safe API-key name.
- [x] 4.4 Implement the detail dialog with row 1 conversation ID/start/latest,
  row 2 account count/total elapsed/dominant user-agent, and a table with exactly
  these displayed columns: Model (effort), Reqs, Total elapsed, Total input (with
  total cache as a subordinate/parenthetical value), Total output, and Total cost.
  Default to Reqs descending and sort every displayed column client-side only.
- [x] 4.5 Add frontend tests for selector default/switching, exact list columns
  and rendering, independent retained URL-backed query state, detail layout and
  client-side sorting,
  detail loading and standard error/retry display for unknown or malformed IDs,
  established em-dash/fallback rendering for nullable optional aggregates, and
  the established empty state for an empty conversation list.

## 5. Verification And Scope Guardrails

- [x] 5.1 Run the targeted backend and frontend test suites for this change.
- [x] 5.2 Run `openspec validate --specs`,
  `openspec validate add-conversation-dashboard --type change --strict`, and
  `git diff --check`.
- [x] 5.3 Confirm the change adds no migration, dependency, setting, README, or
  changelog work and does not synchronize the main capability specs.

## 6. Conversation Dashboard UI Refinement

- [x] 6.1 Make the original-styled list title the single Request
  Logs/Conversations selector and remove the separate selector to its right.
- [x] 6.2 Remove the Conversations filter input, reorder Last request before
  Conversation, and format Last request like the request-log Time column.
- [x] 6.3 Resolve representative account IDs to dashboard account display names,
  with email and ID fallbacks, without changing the list API aggregation key.
- [x] 6.4 Remove the conversation-ID copy action from the details dialog while
  retaining the displayed ID.
- [x] 6.5 Update focused frontend tests and run the relevant frontend and strict
  OpenSpec validation commands.
