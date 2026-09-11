- [ ] `app/modules/proxy/helpers.py`: add `_USAGE_LIMIT_MESSAGE_MARKERS` beside
  the existing `_MODEL_CAPACITY_MESSAGE_MARKERS`, matched
  punctuation-insensitively. In `classify_upstream_failure`, raise an envelope
  whose message asserts the usage limit from `retryable_transient` to
  `rate_limit` ONLY when its normalized code is the `upstream_error` a missing
  code normalizes to, or `invalid_request_error`. A code that already carries
  its own classification decision — rate-limit, quota, `overloaded_error`, any
  other transient code — keeps it, or this override silently reverses two
  existing requirements the delta never declares MODIFIED.
- [ ] `app/core/balancer/types.py` + `helpers.py`: add `excludes_account: bool`
  to `ClassifiedFailure` — the SELECTION predicate, not an exhaustion one. True
  for every walkable class (`rate_limit`, `quota`, `retryable_transient`,
  which includes the code-less burst 429), false only for a model-capacity
  rejection, with a usage-limit message outranking a capacity match. Naming it
  after exhaustion collapses the burst 429 and the capacity 429 to the same
  value and cannot drive the walk. Account selection reads this field; account
  health keeps reading `failure_class`. Do not add a second public classifier.
- [ ] Cover the wordings upstream actually sends. `"The usage limit has been
  reached"` is the passive form the repo's own fixtures use most
  (`tests/unit/test_proxy_http_bridge.py`, `tests/unit/test_proxy_utils.py`,
  `tests/integration/test_automations_api.py` and others); a marker tuple built
  only from the active voice is a no-op on the traffic this change exists for.
  Build at least one test from an observed upstream envelope rather than from
  the marker tuple, and bind the punctuation-insensitive fold with a message
  that actually needs it. Fold `_WEBSOCKET_HANDSHAKE_ERROR_HINTS`
  (`app/core/clients/proxy.py`) into the same predicate — it carries the
  identical blind spot.
- [ ] Preserve client retry guidance across the reclassification. A code-less
  429 that stops being a burst rejection also stops getting
  `_stamp_surfaced_burst_retry_after` (`retry.py`, both the pre-visible site and
  its post-forced-refresh twin), and carries no `error.resets_at` to replace it.
- [ ] Make usage-limit evidence provable inside the request: `handle_rate_limit`
  must record the at-or-above-limit usage sample that `pool_usage_exhaustion`
  requires, the way `handle_quota_exceeded` already does, and the
  `_request_usage_refresh` hook in `_service/streaming/helpers.py` must be gated
  on the classification rather than on `code == USAGE_LIMIT_REACHED`. Without
  this the terminal probe reports a healthy pool however many accounts the walk
  exhausted. Check whether `openspec/specs/usage-refresh-policy/spec.md` needs a
  MODIFIED entry once the gate moves.
- [ ] `app/core/balancer/logic.py`: replace `candidates_remaining: int` with
  `more_candidates_possible: bool` in `failover_decision`; move the
  `non_retryable` check ahead of the candidate check; add
  `MAX_ACCOUNT_ATTEMPTS_CEILING` beside `BURST_SAME_ACCOUNT_MAX_RETRIES` with
  the same "not an operator knob" comment. Its value MUST exceed the largest
  supported pool — this fleet runs 28 accounts, so a ceiling of 16 would silently
  become the ordinary bound and re-introduce exactly the fixed cap this change
  removes. Keep `candidates_remaining` as a deprecated keyword shim for one
  release.
- [ ] Record which bound ended a walk (non-retryable, exhausted pool, deadline,
  ceiling, progress failure). `failover_decision` returning `surface` for all of
  them is what makes the reorder unobservable today.
- [ ] Carve the capacity-recovery re-admission out of the monotone-progress
  check: `_service/streaming/retry.py` already calls
  `excluded_account_ids.discard(...)` when waiting out a local cap, which is a
  legitimate re-admission and must not trip `pool_walk_no_progress`.
- [ ] New `app/modules/proxy/pool_terminal.py`:
  `resolve_pool_terminal_failure(...)` asks `probe_pool_usage_exhaustion`
  exactly once and returns either the canonical `usage_limit_reached` 429 with
  `error.resets_at` or the preserved last per-account failure verbatim.
- [ ] `_service/streaming/retry.py`: convert the bounded
  `for attempt in range(max_attempts)` loop to a guarded walk; record
  `last_account_failure` where the verbatim `raise` stands today (both the
  pre-visible site and its post-forced-refresh twin); raise through
  `resolve_pool_terminal_failure` at the terminal point; add the
  monotone-progress check and its `pool_walk_no_progress` warning.
- [ ] `_service/websocket/mixin.py` and `_service/compact.py`: same walk shape;
  pass `owner_bound` and `same_account_retry_available` on the websocket path,
  which omits them today.
- [ ] `app/modules/proxy/service.py`: delete `_STREAM_MAX_ACCOUNT_ATTEMPTS`,
  `_WEBSOCKET_MAX_ACCOUNT_ATTEMPTS` and `_COMPACT_MAX_ACCOUNT_ATTEMPTS`.
- [ ] Verify the per-account health write stays on the `failover_next` branch
  and is not duplicated by the walk.
- [ ] `tests/unit/test_failover_foundation.py`: keep
  `test_rate_limit_code_takes_precedence_over_capacity_message` passing
  unchanged; add the usage-limit-message truth table; assert
  `excludes_account` is `False` for a capacity message under a rate-limit code,
  `True` for a usage-limit message, and `True` for a code-less burst 429; assert `is_upstream_burst_rejection` is
  `False` for a usage-limit-message 429 and `True` for a model-capacity 429;
  cover the deprecated `candidates_remaining` shim.
- [ ] `tests/integration/test_proxy_transient_retry.py`: A 429 -> B 429 -> C 200
  serves the client with three dispatches and three health writes; all accounts
  exhausted yields one `usage_limit_reached` 429 with `error.resets_at` and one
  probe call; a `non_retryable` failure mid-walk surfaces immediately; an
  owner-bound burst 429 never walks; a drain strategy is byte-identical to
  today; a selector that returns an excluded account triggers
  `pool_walk_no_progress`.
- [ ] `tests/unit/test_streaming_retry_virtual_time.py`: the walk adds no wall
  time between attempts and deadline exhaustion mid-walk terminates with
  `upstream_request_timeout`, not a 429.
- [ ] `tests/integration/test_exhaustion_probe_integration.py`: the probe is
  invoked at most once per request from the terminal path.
- [ ] `tests/simulation/test_proxy_turn_lifecycle_property.py`: for any sequence
  of per-account failures, dispatches are bounded, the excluded set is strictly
  monotone, and there is exactly one health write per attempted account.
- [ ] Confirm `[settings_fields].max` is unchanged at 96 and no new
  `CODEX_LB_*` name is introduced.
- [ ] `openspec validate --specs`, `uv run ruff check`,
  `codex review --base origin/main`.
