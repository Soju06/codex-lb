# Tasks

- [x] 1. Add a compact account-neutral replay verification helper in
  `app/modules/proxy/_service/compact.py` that returns the anchor-free
  `ResponsesCompactRequest` only when the request carries `previous_response_id`, a
  list-shaped `input` with more than one item, an upstream-bound serialization that is
  item-for-item identical to the validated request `input`, passes
  `responses_payload_is_account_neutral_fresh_replay`, and retains prior assistant output
  ahead of new client input via `responses_input_suffix_retains_prior_output`.
- [x] 2. In the `compact_responses` account-selection loop, when selection returns no account
  and the request is pinned only by the previous-response owner, activate recovery for a
  verified payload when the owner loss is quota-caused (persisted
  `RATE_LIMITED`/`QUOTA_EXCEEDED` status at selection time, or a pre-visible quota/rate-limit
  in-request failover of the owner): exclude the owner, drop the pin, strip session/turn
  affinity aliases from upstream-bound headers via
  `without_http_bridge_session_affinity_headers`, and reselect with fallback enabled.
- [x] 3. Keep every other case fail-closed and record `continuity_fail_closed` (surface
  `compact`, reason `owner_account_unavailable`) when the pinned selection failure is
  surfaced: additional turn-state/input-file owner pins on the same owner, session identity
  on the request, session-ownership affinity, non-quota owner loss, and unverifiable
  histories.
- [x] 4. Add unit tests for the verification helper (eligible full resend; missing anchor;
  single-item and string inputs; server-assigned ids; encrypted compaction state; delta
  histories without retained output; transcripts without fresh follow-up input; wire-trimmed
  oversized histories).
- [x] 5. Add integration regression tests at `POST /backend-api/codex/responses/compact`:
  selection-time quota loss recovers on the other account without `previous_response_id`;
  mid-request owner 429 recovers the same way; repeated post-refresh 401 stays owner-bound;
  delta histories, account-scoped histories, session-identified requests, and paused owners
  stay fail-closed with nothing sent to another account.
- [x] 6. Run `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, and the
  unit + compact integration suites; validate the change with strict OpenSpec validation.
