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
  with one combined conversation-ID/user-agent-family search input;
- model-plus-effort detail statistics with client-side column sorting; and
- backend and frontend regression coverage.

Does not cover conversation inference/backfill for historical rows or any change
to proxy routing, request-log capture, or conversation-ID detection. Date and
timeframe controls are intentionally not part of the Conversations view.

Capability ownership for this change is `frontend-architecture`; no
`proxy-runtime-observability` change-level delta is retained.

## Implementation Decisions

- **Aggregation source is request-log rows only.** Both endpoints derive every
  aggregate from `request_logs`; no second store is introduced.
- **One eligible-row scope is shared.** Rows are normalized to a trimmed,
  non-empty conversation identity, exclude `warmup` and `limit_warmup` request
  kinds, and exclude soft-deleted rows (`deleted_at IS NULL`). This keeps list
  and detail totals consistent.
- **Search selects conversations before aggregation.** The list accepts only
  `limit`, `offset`, and `search`. Search is case-insensitive and checks the
  normalized conversation ID and every eligible row's user-agent family. Once a
  conversation matches, all eligible rows in that conversation are aggregated;
  rows that did not contain the matching text remain included.
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

The dashboard list columns are Conversation, Last request, Accounts, API key,
Models, Tokens, Cost, and Details. Account/model remainder values use a smaller
muted `+ N more` secondary line, and cached tokens are subordinate to total
tokens. The selector uses the existing
Radix-style menu conventions and `ChevronDown`, with Request Logs as the default
and `view=conversations` as the URL-backed alternative. Each view's filters and
pagination are retained in separate URL-backed query state; switching views does
not reinterpret, overwrite, or clear the inactive view's state, and conversation
search changes reset the conversation page/offset to zero.

The detail dialog puts conversation ID/start/latest on row one and account
count/total elapsed/dominant user-agent on row two. Its displayed model/effort
table has exactly these columns: Model (effort), Reqs, Total elapsed, Total input
(with total cache as a subordinate/parenthetical value), Total output, and Total
cost. It starts at Reqs descending; every displayed column is sortable
client-side over the single returned page.

## Verification Decisions

- Backend coverage targets the new API routes rather than only helper methods:
  pagination and stable ordering, blank-ID/`warmup`/`limit_warmup`/soft-delete exclusion,
  case-insensitive search selection over normalized IDs and user-agent families
  followed by whole-conversation aggregation, distinct-model list
  representatives and remaining counts, nullable/multiple API keys, cumulative
  elapsed time at both levels, token/cost semantics, exact detail columns,
  default `reqs DESC`, no API sort parameter, encoded blank detail path, and
  unknown-ID 404 behavior.
- Frontend coverage asserts the Request Logs default, selector URL state, one
  search input with no date controls, independent retained URL query state and
  conversation pagination reset, exact list columns and subordinate cached
  tokens, detail loading/error/retry behavior, nullable aggregate fallbacks,
  empty state, detail layout, and client-side-only sorting.
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
