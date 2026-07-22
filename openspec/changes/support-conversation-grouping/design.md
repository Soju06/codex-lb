## Context

Request logs currently capture the inbound user-agent and client IP but do not
retain a conversation identifier. Codex exposes its conversation through the
`thread-id` request header. OpenCode exposes its parent conversation through
`x-parent-session-id`, with `x-opencode-session`, `x-session-id`, and
`x-session-affinity` as ordered fallbacks. Both HTTP and WebSocket request paths
already carry request metadata to the shared request-log persistence layer.

Conversation grouping must support filtering and cost aggregation without
changing request routing or forwarding behavior. Detection therefore remains
observational: unsupported clients and requests without a usable conversation
header continue normally and store a null conversation ID.

## Goals / Non-Goals

**Goals:**

- Detect Codex and OpenCode conversation IDs consistently for HTTP and
  WebSocket requests.
- Make harness detection easy to extend without introducing a plugin system.
- Persist conversation IDs and expose API-level filtering and aggregation.
- Show conversation filtering, cost, and request-count information on the
  dashboard.
- Add distinct conversation counts to dashboard and report statistics.

**Non-Goals:**

- Backfilling conversation IDs for existing request logs.
- Inferring conversations from request bodies, response IDs, accounts, or
  temporal proximity.
- Treating requests without a detected conversation ID as a synthetic
  conversation.
- Separating identical conversation IDs by harness.
- Changing affinity, routing, or upstream header behavior.

## Decisions

### Detection and persistence

1. Define an ordered harness rule table beside the existing request-log
   user-agent metadata helper. Each rule contains a user-agent prefix and an
   ordered list of conversation headers:

   - `opencode` -> `x-parent-session-id`, `x-opencode-session`, `x-session-id`,
     `x-session-affinity`
   - `codex` -> `thread-id`

   A new harness requires one additional rule rather than changes throughout
   the proxy call graph.

2. Match the trimmed user-agent prefix case-insensitively and use the first
   matching rule. Header names are case-insensitive. Use the first non-empty
   configured header and trim only its surrounding whitespace; preserve the
   remaining conversation ID exactly.

3. Unsupported or missing user-agents, unmatched harnesses, and missing or
   empty configured headers produce a null conversation ID. Detection never
   rejects or alters a proxied request.

4. Carry `conversation_id` through the existing HTTP, WebSocket, preflight
   error, compact, control, transcription, file, warmup, thread-goal, and model
   source request-log paths. The repository remains the single persistence
   sink.

5. Add a nullable `request_logs.conversation_id` column and an
   `idx_logs_conversation_id` index in one Alembic migration. Existing rows
   remain null because the original inbound headers cannot be reconstructed.

### Request-log API

6. `GET /api/request-logs` accepts an optional `conversation_id` parameter.
   It composes with all existing filters and participates in the request-count
   cache signature. Pagination does not affect aggregate values.

7. When `conversation_id` is present, the response includes:

   ```json
   {
     "conversation": {
       "requestCount": 12,
       "aggregatedCostUsd": 1.23
     }
   }
   ```

   The response does not duplicate the conversation ID because it is already
   present in the filter. Without the filter, `conversation` is null. A filter
   with no matching rows returns `requestCount: 0` and
   `aggregatedCostUsd: 0`.

8. Conversation request count and cost use the complete active filter set,
   including timeframe, status, model, account, API key, and search filters.
   Cost is the sum of stored `cost_usd` values with an empty result represented
   as zero.

### Dashboard and reports

9. In Request Details, Client IP and Conversation ID share one row so Client IP
   does not consume the full width. A present conversation ID is a clickable,
   no-underline text control. Clicking it closes the dialog, preserves existing
   filters, sets the URL-backed conversation filter, and resets pagination.

10. A removable conversation badge appears between the Statuses control and
    Reset. Its dismiss action clears only the conversation filter and resets
    pagination.

11. When the filtered API response contains `conversation`, insert a summary
    box between the filter row and request-log table. It renders:

    `The conversation ${id} runs ${count} request(s), cost = ${formattedCost}`

    The conversation ID, request count, and formatted cost MUST render as
    styled inline-code values without literal backtick characters.

    An inline suffix lists the other active filters, for example
    `— filters: 7d; statuses: error; model: gpt-5`. The conversation filter is
    not repeated in the suffix.

12. Dashboard overview metrics count distinct, non-empty conversation IDs in
    the selected timeframe. The overview response also exposes a
    `trends.conversations` series with one distinct-count point per configured
    bucket, zero-filled for empty buckets. The Conversations card appears
    between Est. API Cost and Error Rate and uses that series. The summary total
    remains the exact timeframe aggregate and is not calculated by summing trend
    points.

13. Report summary metrics count distinct, non-empty conversation IDs over the
    complete filtered report range. The Conversations card appears immediately
    after Requests.

14. Each daily report row counts distinct, non-empty conversation IDs active
    on that day. A conversation spanning multiple days counts once in each
    applicable daily row but once in the report-wide total. The Conversations
    column appears between Reqs and Input Tokens and participates in sorting and
    CSV export.

## Failure Handling and Verification

- Conversation IDs remain nullable throughout the database, backend schemas,
  and frontend schemas.
- API filtering uses bound query parameters and URL encoding; header values are
  not interpreted beyond whitespace trimming.
- Detection tests cover rule precedence, case-insensitive user-agent prefixes
  and header names, whitespace, OpenCode fallbacks, Codex, and unsupported
  harnesses.
- Regression tests exercise persistence through externally visible HTTP and
  WebSocket paths rather than only testing the helper.
- API tests cover conversation-only filtering, composition with other filters,
  zero matches, aggregation, pagination independence, and count-cache
  isolation.
- Dashboard tests cover detail clicks, URL state, badge dismissal, inline
  filter descriptions, summary placement, and metric ordering.
- Report tests cover report-wide and daily distinct counts, summary/column
  ordering, sorting, and CSV export.
- Migration verification covers upgrade, downgrade, and a single Alembic head.
- OpenSpec validation and focused backend/frontend checks run before the change
  is considered complete.

## Risks / Trade-offs

- [Conversation headers contain arbitrary client values] -> Store only values
  selected by known harness rules and treat them as opaque strings.
- [Identical IDs appear under different harnesses] -> Intentionally aggregate
  them together; harness-scoped identity is outside this change.
- [Distinct counts add query work] -> Reuse existing dashboard/report aggregate
  queries and add an equality index for conversation-filtered request lists.
- [New proxy metadata is missed on an error path] -> Extend the shared logging
  contract and cover both HTTP preflight failures and WebSocket finalization in
  regression tests.
- [Other filters make a conversation summary look partial] -> Display the
  active filters inline with the summary sentence.

## Migration Plan

Deploy the additive nullable column and index before code begins writing or
querying `conversation_id`. No data backfill or feature flag is required.
Rolling back the application stops detection and filtering; downgrading the
migration removes the index and column. Existing request behavior is unchanged
throughout because conversation detection is observational.

## Open Questions

None.
