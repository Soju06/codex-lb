# Conversation Dashboard Design

## Purpose

Give operators a first-class aggregate view of proxied conversations on top of
the request-log rows that already carry a non-empty `conversation_id`, without
changing proxy routing or conversation detection.

## Scope

Covers:

- two authenticated dashboard APIs (`GET /api/conversations` and
  `GET /api/conversations/{conversation_id}`);
- a switchable dashboard view (Request Logs default, Conversations alternative)
  whose section title is the view selector;
- model-plus-effort detail statistics with client-side column sorting; and
- backend and frontend regression coverage.

Does not cover conversation inference/backfill for historical rows or any change
to proxy routing, request-log capture, or conversation-ID detection. A bounded
day-range selector (`1d`/`7d`/`30d`, default `7d`, no unbounded "all") drives the
list's server-authoritative `timeframe` parameter and caps activity lookback.
Summary aggregates for selected conversations intentionally retain eligible
history.

Capability ownership for this change is `frontend-architecture`; no
`proxy-runtime-observability` change-level delta is retained.

## Implementation Decisions

- **Aggregation source is request-log rows only.** Both endpoints derive every
  aggregate from `request_logs`; no second store is introduced.
- **Write-time identity normalization and indexed reads.** Production log writes
  normalize ASCII padding and blank conversation IDs before persistence. The
  conversation list, facets, and detail hot paths therefore predicate and group
  on the raw `request_logs.conversation_id` column, preserving the
  `idx_logs_conversation_id` access path while excluding null/blank production
  IDs. Rows exclude `warmup` and `limit_warmup` request kinds and soft-deleted
  rows (`deleted_at IS NULL`), keeping list, detail, and dashboard conversation
  totals consistent.
- **Search selects conversations before aggregation.** The list accepts `limit`,
  `offset`, `search`, legacy `since`, and server-authoritative `timeframe`.
  Search is case-insensitive and checks the
  normalized conversation ID and every eligible row's user-agent family. Once a
  conversation matches, all eligible rows in that conversation are aggregated;
  rows that did not contain the matching text remain included.
- **Bounded windows and activity semantics.** The API supplies a rolling 30-day
  default when no window is supplied and caps explicitly older legacy `since`
  values at that bound. Incoming timezone-aware `since` values are converted to
  naive UTC. Dashboard `timeframe` values are resolved by the server from the
  shared overview configuration, and `timeframe` and `since` are mutually
  exclusive. A conversation qualifies when any eligible row has
  `requested_at >= effective_since`, so long-running conversations remain
  visible while active in the selected window. The grouped summary uses the raw
  conversation ID and aggregates all eligible rows for a selected conversation,
  including rows before `effective_since`;
  membership is enforced by an in-window aggregate condition rather than a
  global pre-window ID set or anti-join. After page membership is selected, the
  account, API-key, and model facet queries use the same full eligible-row
  scope as the summary, so representatives and remaining counts describe the
  entire conversation rather than only its recent activity. The grouped total
  is display-only pagination metadata and is served from the same short-TTL
  per-signature cache as the request-log listing total (issue #1340), keyed by
  `search` and semantic window identity (`timeframe` or effective `since`).
- **List order is stable.** Groups are ordered by `lastRequest DESC`, then
  normalized conversation ID ASC; pagination is applied after this order.
- **Representative selection is deterministic.** Representatives use
  `(request_count DESC, latest_requested_at DESC, lexical value ASC)`. For the
  conversation list, account values use their account ID and model values are
  grouped by distinct model across all reasoning efforts, with the model name as
  the lexical value. API-key values use the API-key ID. Dominant user-agent
  selection uses the user-agent family. Conversation details alone group rows
  by `(model, reasoning_effort)` and use that pair as the lexical key for detail
  ordering.
- **The common API-key case is one key.** Nullable and multiple-key data still
  has defined behavior: null keys are not candidates; multiple non-null keys use
  the representative ordering; no safe key candidate produces null API-key
  fields. The UI displays the existing dashboard-safe API-key name, never secret,
  hash, or plaintext material.
- **Total elapsed time is cumulative per-request latency.** Conversation and
  model-plus-effort totals both use `SUM(COALESCE(latency_ms, 0))`, never the
  wall-clock span between the earliest and latest request.
- **Output tokens fall back to reasoning tokens.** Per-row output totals use
  `COALESCE(output_tokens, reasoning_tokens)` so aggregate totals match existing
  usage-summary semantics.
- **Cached tokens retain existing clamping.** A null cached value remains null;
  otherwise cached input is clamped to `[0, input_tokens]` when input tokens are
  present, matching `cached_input_tokens_from_log`.
- **No schema or package work.** Existing indexes and dependencies are reused.
  No migration, setting, dependency, README, or changelog change is planned.

## Wire and UI Notes

The list row contains exactly these fields from the frontend-architecture delta:
`conversationId`, `lastRequest`, `representativeAccount`,
`remainingAccountCount`, `apiKeyId`, `apiKeyName`, `representativeModel`,
`remainingModelCount`, `totalTokens`, `cachedInputTokens`, and
`totalCostUsd`. Model values and remaining counts represent distinct models,
regardless of reasoning effort. The list envelope contains pagination metadata
and rows only, not a response summary object.

The dashboard list columns are Last request, Conversation, Accounts, API key,
Models, Tokens, Cost, and Details. Last request uses the request-log Time
column's two-line time/date presentation. Account/model remainder values use a
smaller muted `+ N more` secondary line, and cached tokens are subordinate to
total tokens. Account IDs remain the API aggregation key, while the frontend
resolves the representative account through the dashboard account summaries and
displays `displayName`, then email, then the ID as the final fallback.

The original uppercase section-title typography is retained and the title itself
becomes the single accessible Radix-style selector trigger with `ChevronDown`.
Request Logs remains the default and `view=conversations` remains the URL-backed
alternative. The separate selector to the title's right is removed. Each view's
pagination and remaining filters retain separate URL-backed query state;
switching views does not reinterpret, overwrite, or clear inactive state. The
Conversations view does not render a free-text filter input above its list.

A day-range selector with exactly `1d`, `7d`, and `30d` options (default `7d`, no
unbounded "all") is rendered at the top-right of the dashboard page, alongside
the refresh action, shown only while the Conversations view is active. It
mirrors the existing dashboard overview timeframe selector's values and default.
The selected value is persisted in the URL as `conversationTimeframe`, resets
pagination to offset 0 on change, and is sent to the list endpoint as the
symbolic `timeframe` parameter. The server derives the effective activity window
from its own UTC clock; the browser does not generate `since`. This bounds
activity membership while summary and page-facet aggregates for selected
conversations intentionally retain eligible history. While Conversations is
active, the dashboard overview/statistics query uses this same selected
conversation timeframe, even when the independently retained overview
timeframe differs in the URL. The short-TTL grouped-count cache (pre-grouping
`total`) avoids recomputing pagination metadata on every repeated poll, with a
stable semantic key for unchanged search and timeframe state.

The detail dialog puts conversation ID/start/latest on row one and account
count/total elapsed/dominant user-agent on row two. Its displayed model/effort
table has exactly these columns: Model (effort), Reqs, Total elapsed, Total input
(with total cache as a subordinate/parenthetical value), Total output, and Total
cost. It starts at Reqs descending; every displayed column is sortable
client-side over the single returned page. Conversation ID remains visible but
does not render a copy action.

An empty result on the initial Conversations page retains the established empty
state without pagination controls. If a later page becomes empty because the
rolling window changes or rows are removed, the empty state is shown together
with pagination controls so the operator can return to an earlier page.

## Verification Decisions

- Backend coverage targets the new API routes rather than only helper methods:
  pagination and stable ordering, blank-ID/`warmup`/`limit_warmup`/soft-delete exclusion,
  case-insensitive search selection over normalized IDs and user-agent families
  followed by whole-conversation aggregation, distinct-model list
  representatives and remaining counts, nullable/multiple API keys, cumulative
  elapsed time at both levels, token/cost semantics, exact detail columns,
  default `reqs DESC`, no API sort parameter, encoded blank detail path, and
  unknown-ID 404 behavior. The `since` activity-in-window filter is covered by a
  conversation that spans the boundary (included) and one with no in-window
  activity (excluded), plus search/pagination composition, the agreement between
  list and overview conversation counts when rows are soft-deleted, and the
  short-TTL grouped-count cache behavior under a positive TTL, including
  server-authoritative timeframe identity.
- Frontend coverage asserts the Request Logs default, title-styled selector URL
  state with no duplicate right-side selector, no conversation free-text filter
  input, the day-range selector (options, default, URL-backed
  `conversationTimeframe`, `timeframe` serialization without browser-generated
  `since`, pagination reset, and
  per-view state independence), independent retained URL query state, exact
  reordered list columns, shared request-log time presentation, human-readable
  account labels, subordinate cached tokens, absence of the conversation-ID copy
  action, detail loading/error/retry behavior, nullable aggregate fallbacks,
  empty state, retained pagination controls for an empty later conversation
  page, detail layout, and client-side-only sorting.
- OpenSpec validation and whitespace validation must pass before this change is
  considered ready. Main capability specs are not synchronized by this change.

## Parallel Execution Shape

After approval, implementation can split into:

1. Backend: shared eligible-row scope, list/detail repository methods, service,
   API routes, and backend tests.
2. Frontend: Request Logs/Conversations selector, Conversations list and detail,
   and frontend tests.
3. Verification: targeted tests, OpenSpec validation, whitespace validation,
   and scope-guard checks for forbidden migration/dependency/settings/docs work.

The frontend workstream depends only on the agreed response shapes, so it can
proceed in parallel with the backend once the spec artifacts are written.
