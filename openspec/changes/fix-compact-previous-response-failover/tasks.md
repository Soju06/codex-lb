# Tasks

- [x] 1. Add a compact account-neutral replay verification helper in
  `app/modules/proxy/_service/compact.py` that returns the anchor-free
  `ResponsesCompactRequest` only when the request carries `previous_response_id`, a
  list-shaped `input` with more than one item, and the upstream-bound payload without the
  anchor passes `responses_payload_is_account_neutral_fresh_replay`.
- [x] 2. In the `compact_responses` account-selection loop, when selection returns no
  account and the request is pinned only by the previous-response owner, activate recovery
  for a verified payload: exclude the owner, drop the pin, blank sticky affinity, strip
  session/turn affinity aliases from upstream-bound headers via
  `without_http_bridge_session_affinity_headers`, and reselect with fallback enabled.
  Record `continuity_fail_closed` (surface `compact`, reason `owner_account_unavailable`)
  when the payload is not verified and the pinned selection failure is surfaced.
- [x] 3. Add unit tests for the verification helper (eligible full resend; missing anchor;
  single-item input; server-assigned ids; encrypted compaction state; account-scoped file
  handles) and for the untouched turn-state strict-pin constraint.
- [x] 4. Add integration regression tests at `POST /backend-api/codex/responses/compact`:
  quota-exhausted owner at selection time recovers on the other account without
  `previous_response_id` and without stale affinity headers; mid-request owner 429 recovers
  the same way; non-neutral payload keeps today's failure and never crosses accounts.
- [x] 5. Run `uv run ruff check`, `uv run ty check`, and the unit + integration proxy test
  suites; validate the change with strict OpenSpec validation.
- [x] 6. Gate recovery on quota-caused owner loss only: allow it when the pinned owner was
  never used for the request at selection time, or when a pre-visible quota / rate-limit
  failover excluded the owner mid-request. Post-selection authentication, refresh, transport,
  and transient exclusions of the pinned owner keep their existing owner-bound handling, with
  an integration regression test at `POST /backend-api/codex/responses/compact` for the
  repeated-401 path.
