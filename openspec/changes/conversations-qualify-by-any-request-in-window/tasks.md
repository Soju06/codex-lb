## 1. Core Repository Change

- [x] 1.1 In `app/modules/request_logs/repository.py` `list_conversations()` (~lines 203-324), remove the `has_pre_window_row` correlated subquery (~lines 249-264) so a conversation is qualified by having at least one in-window `requested_at` row rather than by its earliest row being inside the window. Preserve the "at least one in-window row" invariant with bounded distinct candidate IDs, then aggregate full history only for those IDs.
- [x] 1.2 Verify `get_conversation_details()` (~lines 361-438) needs no change — it already aggregates all eligible logs for a conversation ID regardless of window. Add an inline comment if useful to document that the detail path is intentionally window-agnostic.
- [x] 1.3 Confirm `list_conversations()` in `app/modules/request_logs/service.py` (~lines 194-209) and the endpoint in `app/modules/request_logs/api.py` (~lines 132-149) need no change (pass-through). Leave the 30-day `_CONVERSATION_MAX_LOOKBACK` cap intact.

## 2. Test Rewrite (keystone + downstream)

- [x] 2.1 Rewrite `tests/integration/test_conversations_api.py::test_since_filter_excludes_conversation_started_before_window` (~line 818) to the new semantics: a conversation with pre-window rows plus an in-window row MUST be included when `since` is inside the window. Rename the test accordingly (e.g. `test_since_filter_includes_conversation_active_in_window`).
- [x] 2.2 Audit and update other assertions in `tests/integration/test_conversations_api.py` whose fixtures place pre-window rows and whose expectations encode the old first-request gate. Cover the four spec scenarios: long-running conversation appears when active; conversation with no in-window rows excluded; conversation starting inside the window still included; bare request bounded to last 30 days of activity.
- [x] 2.3 Add a regression test asserting that a surfaced long-running conversation reports a `firstRequest` strictly earlier than `since` (true global `MIN(requested_at)`), matching the "Conversation start timestamp is the true earliest request" requirement.
- [x] 2.4 Audit `tests/unit/test_request_logs_repository.py`, `tests/unit/test_request_logs_service.py`, `tests/integration/test_request_logs_filters.py`, `tests/integration/test_dashboard_overview.py`, `tests/unit/test_dashboard_trends.py`, `tests/integration/test_reports_api.py`, and `tests/unit/test_reports_repository.py` for assertions that depended on the first-request gate and update them to the any-in-window semantics.
- [x] 2.5 Add a regression test asserting the conversations list and the dashboard activity/trends aggregation agree for the same window (both count `conv-a` with pre-window + in-window rows; both exclude `conv-b` with only out-of-window rows), matching the "Conversation list membership agrees with dashboard activity aggregations" requirement.

## 3. OpenSpec & Context Docs

- [x] 3.1 Run `openspec validate --strict conversations-qualify-by-any-request-in-window` and resolve any errors before opening the PR.
- [x] 3.2 After the change is verified and prior to archive, sync the new `conversations-api` capability spec into `openspec/specs/conversations-api/spec.md` (it is a new capability, not a delta).
- [x] 3.3 Add `openspec/specs/conversations-api/context.md` documenting: conversations are a derived view over `request_logs` (no stored entity); membership is any-in-window; `firstRequest` on list entries and `start` on detail responses report the true global earliest eligible request and may fall outside the window by design; the prior first-request gate was removed to resolve an inconsistency with dashboard activity/trends aggregations; one worked example of a long-running conversation in a 30-day window.
- [x] 3.4 N/A — no `docs/` page references `/api/conversations`; creating one would violate the simplicity budget. Linked from `context.md` to related capabilities instead.

## 4. PR Readiness

- [ ] 4.1 Confirm the PR body marks the `since` semantics change as **BREAKING**, references the new `conversations-api` capability, and notes that dashboard activity/trends agreement is a resolved inconsistency rather than a regression.
- [ ] 4.2 Include before/after evidence (command output or dashboard screenshots) demonstrating a long-running conversation now appears in a recent window where it previously did not. Required because this is a dashboard-visible contract change.
- [x] 4.3 Run the focused test suite: `uv run pytest tests/integration/test_conversations_api.py tests/unit/test_request_logs_repository.py tests/unit/test_request_logs_service.py -q` and ensure green.
- [x] 4.4 Run `openspec validate --specs` to confirm the merged spec tree is well-formed before merge.

## 5. Bound Activity Candidate Discovery

- [x] 5.1 Add a bounded `DISTINCT conversation_id` candidate query in
  `list_conversations()` using the effective `since` and all eligibility
  predicates, then constrain the full-history summary and facets with those
  candidate IDs instead of a grouped `HAVING` membership filter.
- [x] 5.2 Apply the same `requested_at >= since` bound to search candidate
  discovery while preserving full-history aggregates for selected IDs.
- [x] 5.3 Add regression coverage that captures the emitted query shape,
  proves inactive history is pruned before grouping, proves surfaced summaries
  retain pre-window history, and covers the bounded search candidate path.
- [x] 5.4 Run focused conversation tests, OpenSpec validation, and backend
  static checks; record the evidence before marking this section complete.
