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
- [x] 7. Verify the replay history against the serialized upstream-bound payload, not the
  request model: require the wire `input` to keep more than one item after every compact
  serializer transformation (poisoned local-compact fallback stripping can collapse a
  multi-item history to one message) and run the account-neutral rules on the same serialized
  payload, with unit and compact-route regression tests.
- [x] 8. Require the serialized wire `input` to be item-for-item identical to the validated
  request `input`, so an oversized history trimmed to a head, trim marker, and tail (or any
  other serializer-dropped history that still leaves two or more items) fails closed instead of
  replaying an incomplete conversation. Guard the speculative serialization against
  `ClientPayloadError` / `ValidationError`, and cover the trimmed case with unit and
  compact-route regression tests.
- [x] 9. Require independently trusted proxy-side proof that the request still carries the
  anchored conversation before dropping the anchor: add
  `DurableBridgeSessionCoordinator.lookup_previous_response_target`, and gate recovery on a
  durable record that names the pinned owner (or no account) and records an input prefix count
  and fingerprint the request `input` strictly extends, reusing
  `_input_prefix_matches_stored_context`. Missing records, missing prefixes, owner mismatches,
  fingerprint mismatches, and failed lookups stay owner-bound, with unit and compact-route
  regression tests for each.
- [x] 10. Also require the recorded prefix to be followed by the anchored response's retained
  output before any new client input via `responses_input_suffix_retains_prior_output`, so a
  resend of the recorded input plus only a new user turn fails closed instead of compacting a
  conversation that is missing the anchored assistant output, with unit and compact-route
  regression tests.
- [x] 11. Bind the prefix proof to the requested anchor: because the recorded prefix count and
  fingerprint are session-level and every later response registration overwrites them, require
  the durable snapshot's `latest_response_id` to equal the requested `previous_response_id`, so
  an older alias resolving to a session that has moved on stays owner-bound. Cover it with a
  unit case and a compact-route regression test that registers a later response on the same
  durable session.
