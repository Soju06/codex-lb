## Context

`/api/conversations` lists conversations derived purely from `request_logs` grouped by `conversation_id` (there is no stored conversation entity). Today, `list_conversations()` in `app/modules/request_logs/repository.py:203-324` qualifies a conversation for a `since` window via a correlated `has_pre_window_row` subquery (`repository.py:249-264`): if **any** row for that `conversation_id` has `requested_at < since`, the whole conversation is excluded. This means a long-running conversation that started before the window but is still active in it is invisible to the listing.

Meanwhile the dashboard activity/trends aggregations — `aggregate_conversations_by_bucket()` (`repository.py:616-644`) and `_aggregate_activity()` (`repository.py:697-706`) — already count conversations by **any** row in the window, with no first-request gate. The two views disagree about what belongs to a range.

`get_conversation_details()` (`repository.py:361-438`) already aggregates all
eligible logs for a given `conversation_id` regardless of window, so it is
unaffected by which side of the gate we pick.

## Goals / Non-Goals

**Goals:**
- Qualify a conversation for `/api/conversations?since=...` by **any** request in the window, not by where it started.
- Remove the `has_pre_window_row` correlated subquery from the hot path.
- Bring the listing endpoint into agreement with the already-correct dashboard activity/trends aggregations.
- Establish a normative spec for the `conversations-api` capability (none previously existed).

**Non-Goals:**
- Introducing a stored conversation entity. Conversations remain a derived view over `request_logs`.
- Changing `firstRequest` (list) / `start` (detail) semantics. They stay as
  `MIN(requested_at)` over all eligible logs — true conversation age, not
  window-relative position. No new field is introduced.
- Changing the 30-day hard cap (`_CONVERSATION_MAX_LOOKBACK`, `api.py:33`). The cap stays; only its effect shifts from "can't look further back than 30 days for starts" to "can't look further back than 30 days for activity."
- Touching proxy follow-up / owner resolution (`find_latest_owner_record_for_response_id` → `_resolve_websocket_previous_response_owner`). That path uses `request_id` matching, not conversation grouping.
- Touching `get_conversation_details()`. It is already window-agnostic.

## Decisions

### Decision 1: Delete the `has_pre_window_row` gate rather than invert it

**Choice:** Remove the subquery entirely; use a distinct candidate-ID query
filtered by `requested_at >= since` to keep only conversations with at least one
in-window row before aggregating their full history.

**Why not invert:** "Invert" would mean "include conversation only if it has a pre-window row" — that's the opposite of the goal. The real choice was remove vs. narrow. Removing is correct: the `since` filter on candidate discovery enforces "at least one in-window row exists" before the full-history aggregate runs, because the gate was the only thing excluding conversations that *also* had pre-window rows.

**Alternative considered:** Keep the subquery but flip its predicate to "only include if NO pre-window row" — rejected, that's the current behavior restated.

### Decision 2: Keep the external start fields as true global `MIN(requested_at)`

**Choice:** `firstRequest` on list entries and `start` on detail responses continue to report the earliest eligible log for the conversation across all time, even when that timestamp falls outside the `since` window.

**Rationale:** The field reports conversation age, not window position. Clamping to window-start would hide real history and mislead operators about how long-running a conversation is. Adding a separate `window_first_request_at` field was considered and rejected (user decision): no information loss justifies a schema/contract change here, and the dashboard already surfaces the in-window rows themselves.

**Trade-off accepted:** A conversation surfaced in a 30-day window may display `firstRequest` from 200 days ago. This is intentional and will be documented in the capability context.

### Decision 3: Establish new `conversations-api` capability rather than delta an existing one

**Choice:** Create `openspec/specs/conversations-api/spec.md` as a new capability.

**Why:** Grep across `openspec/specs/**/spec.md` shows no capability owns `/api/conversations`. `query-caching` owns `/api/request-logs` (the row-listing endpoint), not the conversations aggregate. Dashboard trends requirements live in `frontend-architecture` but describe the UI cards, not the conversations endpoint contract. There is nothing to delta — we are establishing the normative contract for the first time, pinned to the new membership rule.

This decision records the initial capability introduced by this change; the
candidate-discovery requirement below extends that same capability rather than
creating a second owner.

### Decision 4: Keep the 30-day lookback cap

**Choice:** `_CONVERSATION_MAX_LOOKBACK = timedelta(days=30)` is retained.

**Rationale:** It bounds operator-facing result size and query cost. Its meaning shifts slightly (activity lookback, not start lookback), but the value remains appropriate. Reconsider only if operators report needing longer activity history — not in scope here.

### Decision 5: Discover active candidates before aggregating full history

**Choice:** When `since` is present, first select distinct eligible
`conversation_id` values with `requested_at >= since` (and any search
predicate), then constrain the full-history summary and facet aggregates with
`conversation_id IN` against that candidate set.

**Rationale:** The activity predicate is a membership gate, not an aggregate
metric. Keeping it in `HAVING` forces the database to group every retained
request-log row before it can discard inactive conversations. The candidate
query can use the existing `requested_at` and `conversation_id` indexes, while
the second phase preserves the contract that `firstRequest`, request counts,
token totals, and costs include all eligible history for each surfaced
conversation. Search is applied in the candidate phase so a user-initiated
search does not independently scan unbounded history.

**Trade-off:** Search terms are evaluated against eligible activity in the
requested window when `since` is supplied. This matches the listing's active
conversation scope and avoids surfacing a currently active conversation only
because a historical user-agent value matched.

## Risks / Trade-offs

- **[BREAKING semantics] `since` filtering returns a different row set for any window that intersects long-running conversations.** → Mitigation: document the semantic shift explicitly in the capability `context.md` and in the PR body; flag as BREAKING in the proposal. No migration path is possible (this is a read view), so callers must adapt.
- **Test churn is larger than code churn (~15–30 assertions across ~5 files vs. ~15 lines of code).** → Mitigation: the canonical test `test_since_filter_excludes_conversation_started_before_window` (`test_conversations_api.py:818`) is the keystone — rewriting it to assert inclusion anchors the rest. Other count/pagination tests need fixture/expectation updates but no new fixtures.
- **`firstRequest` outside the window may surprise operators.** → Mitigation:
  document in `context.md`; the field reports origin, not window entry.
- **Agreement with dashboard aggregations is a fix, but could itself surface latent discrepancies operators had internalized.** → Mitigation: record in `context.md` as a resolved inconsistency, not a regression.

## Migration Plan

No data migration. Pure read-path semantic change.

Deploy steps:
1. Land the `repository.py` change + rewritten tests in one PR.
2. Ship the new `conversations-api` capability spec in the same PR.
3. No feature flag — the change is small, localized, and the old behavior was internally inconsistent with the dashboard aggregations.

Rollback: revert the PR. No schema or data consequences.

## Open Questions

None outstanding. The two semantic forks (start-field display, new-capability vs. delta) were resolved during proposal drafting.
