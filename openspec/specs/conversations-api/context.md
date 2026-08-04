# Conversations API — Context

## Purpose / Scope

`/api/conversations` and `/api/conversations/{id}` expose a **derived view** over `request_logs`: rows are grouped by the nullable `conversation_id` string column, and aggregates (`MIN`/`MAX`/`COUNT`/`SUM`) are computed on read. There is **no stored conversation entity**, no foreign key from `request_logs` to a conversations table, and no write path that creates a conversation row. The capability exists purely to pin the read-side contract (membership, windowing, timestamps) for operators and dashboard clients.

**Non-goals:** conversation ownership resolution for routing (that lives in `sticky-session-operations` and uses `request_id` / `previous_response_id`, not grouping); conversation archive storage (that lives in `proxy-runtime-observability`); UI rendering of conversation cards (that lives in `frontend-architecture`).

## Why membership is "any in-window request," not "first request in window"

Earlier versions of `list_conversations()` qualified a conversation for a `since` window only when its **earliest** `request_logs` row fell inside the window, enforced by a correlated `has_pre_window_row` subquery that excluded any conversation with a row predating `since`. This conflicted with the dashboard activity and trends aggregations (`aggregate_conversations_by_bucket`, `_aggregate_activity`), which have always counted conversations by **any** row in the window. The two views disagreed about what conversations belonged to a range, and a long-running conversation that started before the window but was still active inside it was invisible to the listing.

The change removed the gate and now first selects distinct eligible conversation IDs with `requested_at >= since`, so a conversation qualifies when it has at least one in-window row. The full-history summary then aggregates only rows belonging to those candidate IDs. This brings the listing into agreement with the dashboard aggregations without making every poll group the entire retained history. The semantic shift is **BREAKING** for any caller that relied on "started in window" semantics — totals, pagination, and facets for a given `since` value all change.

**Alternative considered and rejected:** keep the gate, fix the dashboard aggregations to match. Rejected because "activity in window" is the more useful operator mental model for a conversations listing — an operator investigating recent traffic wants to see conversations that were recently active, including ones that started earlier.

## Why the start fields are the true global earliest (not window-relative)

`firstRequest` on list entries and `start` on detail responses are `MIN(requested_at)` over all eligible rows for the conversation, even when that timestamp falls outside the `since` window. These fields report conversation **age/origin**, not window-relative position. Clamping to the window boundary was considered and rejected: it would hide real history and mislead operators about how long-running a conversation is. Adding a separate `window_first_request_at` field was also considered and rejected — no information loss justifies a schema/contract change here, and the in-window rows themselves are already surfaced.

**Operator-visible consequence:** a conversation surfaced in a 30-day window may display a `firstRequest` from 200 days ago. This is intentional, not a bug.

## Constraints

- **No stored entity.** Conversations cannot be created, updated, or deleted directly. Their existence and shape are entirely a function of `request_logs` content. Soft-deleting `request_logs` rows (via `deleted_at`) removes them from the derived view.
- **30-day lookback cap.** `_CONVERSATION_MAX_LOOKBACK = timedelta(days=30)` bounds the effective `since` for both bare and explicit requests. It bounds **activity** lookback, not start lookback. Operators needing longer history must query `request_logs` directly.
- **Performance.** The listing first discovers distinct active conversation IDs
  with the `requested_at` bound, then groups eligible full-history rows only for
  those IDs through the indexed `conversation_id` predicate. Search predicates
  participate in the same bounded candidate phase. Page facets use the selected
  IDs while retaining full-history semantics, and detail queries use raw
  equality, preserving the plain `conversation_id` index access path for those
  targeted reads.
- **Soft-delete / warmup exclusion.** The underlying `request_logs` filters exclude soft-deleted rows and warmup kinds; the derived conversation view inherits those exclusions automatically.

## Failure modes / edge cases

- **Empty `conversation_id`.** Rows with null/blank `conversation_id` are excluded from grouping; they do not form an "untitled" conversation.
- **Header-derived identity.** `conversation_id` is populated from client HTTP headers (e.g. `x-parent-session-id` for OpenCode clients, `thread-id` for Codex CLI). Detection lives in `proxy-runtime-observability`. A malformed or unknown header yields a null conversation ID, which the listing then excludes.
- **Cross-account conversations.** A conversation may span multiple accounts
  (the grouping is by ID, not by account). The list response exposes a
  representative account and remaining-account count, with account facets used
  for selection; this is expected, not an error.
- **Long history.** A conversation with years of history aggregates over all of it for `firstRequest`/`start` and summary totals. The 30-day lookback cap on `since` bounds which conversations surface, but each surfaced conversation still reports its full-history summary aggregates.

## Example

Operator opens the dashboard's 30-day conversations view on 2025-07-27. Conversation `conv-long` has rows at:
- `2025-01-15` (start)
- `2025-06-02`
- `2025-07-26` (yesterday)

The conversation **appears** in the 30-day list because it has a row at `2025-07-26` (inside the window). Its `firstRequest` reports `2025-01-15` (the true global minimum, ~192 days before the window start). Its `lastRequest` reports `2025-07-26`, and its `requestCount` reports `3` (all eligible rows). The dashboard activity aggregation for the same window also counts `conv-long` once — the two views agree.

Before this capability's normative membership rule was pinned, the listing would have **excluded** `conv-long` entirely (its earliest row predates the window), while the dashboard aggregation counted it — the inconsistency this capability resolves.

## Related specs

- `proxy-runtime-observability` — `conversation_id` detection from client headers, persistence model, archive lookup.
- `frontend-architecture` — dashboard rendering of conversation trends, badges, and detail dialogs.
- `sticky-session-operations` — conversation ownership for **routing** (uses `request_id`/`previous_response_id`, not the grouping contract here).
- `query-caching` — owns `/api/request-logs` (the row listing), distinct from `/api/conversations` (the aggregate view).
