## Why

The `/api/conversations` listing endpoint today treats a conversation as "in window" only when its **earliest** `request_logs` row falls inside the `since` window. This conflicts with the dashboard activity/trends aggregations (`aggregate_conversations_by_bucket`, `_aggregate_activity`), which already count conversations by **any** row in the window. The two views disagree about what conversations belong to a given time range, and an operator inspecting a long-running conversation in a recent window sees nothing. We should qualify a conversation by activity in the window, not by where it started.

## What Changes

- **BREAKING (semantics)**: `GET /api/conversations?since=...` membership changes from "conversations whose first request is in the window" to "conversations that have any request in the window." Totals, pagination, and facets for a given `since` will shift (more conversations returned in any window that contains activity from long-running conversations).
- Remove the `has_pre_window_row` first-request gate from `list_conversations()` (`app/modules/request_logs/repository.py:249-264`).
- `firstRequest` (list) / `start` (detail) semantics are **unchanged**: they remain `MIN(requested_at)` over all eligible logs for the conversation, so a conversation surfaced in a 30-day window may still report a `firstRequest` well before the window. This is intentional — the field reports true conversation age, not window-relative position. No new field is introduced.
- The 30-day hard cap on `since` (`_CONVERSATION_MAX_LOOKBACK`, `api.py:33`) is retained; only its effect changes from "can't look further back than 30 days for starts" to "can't look further back than 30 days for activity."
- `get_conversation_details()` requires **no change** — it already aggregates
  all eligible logs for a conversation ID regardless of window.
- Aligns the conversations list with the already-correct dashboard activity/trends aggregations, resolving the existing inconsistency between them.

## Capabilities

### New Capabilities
- `conversations-api`: The `/api/conversations` listing and detail endpoints — membership/windowing semantics, sort/pagination contract, and the relationship of the derived conversation view to underlying `request_logs`. (No spec existed previously; this change establishes the normative contract and pins the new membership rule.)

### Modified Capabilities
<!-- None. `query-caching` owns `/api/request-logs` (the row-listing endpoint), not `/api/conversations`, so it is unaffected. Dashboard trends/activity aggregations already use the any-in-window rule and need no requirement change. -->

## Impact

- **Code**: `list_conversations()` in `app/modules/request_logs/repository.py` discovers bounded activity/search candidates before aggregating full history for selected IDs. `service.py` and `api.py` are pass-through and need no change. `get_conversation_details()` needs no change.
- **API contract**: `/api/conversations` response shape is unchanged, but `since` filtering semantics change. Clients/dashboards relying on "started in window" will see different row sets.
- **Performance**: the activity membership phase is bounded by a `DISTINCT conversation_id` query filtered by `requested_at >= since`; the subsequent summary/facet aggregation still reads full eligible history only for those candidate IDs. Search uses the same bounded candidate phase.
- **Tests**: ~15–30 assertions across ~5 files must be rewritten. The primary one is `tests/integration/test_conversations_api.py:818` (`test_since_filter_excludes_conversation_started_before_window`), which directly encodes the current first-request gate and must be inverted to assert inclusion. Other count/pagination tests that assume first-request gating need their fixtures/expectations updated.
- **Consistency**: brings the conversations list into agreement with `aggregate_conversations_by_bucket` and `_aggregate_activity`, which already count by any-in-window. Worth recording in the capability context as a fix, not just a behavior change.
- **Unaffected**: proxy follow-up/owner resolution (`find_latest_owner_record_for_response_id` → `_resolve_websocket_previous_response_owner`) uses `request_id` matching, not conversation grouping — untouched. `request_logs` write path and `conversation_id` header extraction are untouched.
- **OpenSpec**: establishes the `conversations-api` capability spec (none existed). No migration needed; this is a read-path semantic change only.
