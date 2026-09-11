- [ ] `app/modules/proxy/helpers.py`: add `_USAGE_LIMIT_MESSAGE_MARKERS` beside
  the existing `_MODEL_CAPACITY_MESSAGE_MARKERS`, matched
  punctuation-insensitively. In `classify_upstream_failure`, raise a code-less
  or non-rate-limit-code envelope whose message asserts the usage limit from
  `retryable_transient` to `rate_limit`. Leave every coded envelope's
  classification exactly as it is.
- [ ] `app/core/balancer/types.py` + `helpers.py`: add `account_exhaustion: bool`
  to `ClassifiedFailure`, set false for a model-capacity message even under a
  rate-limit code and true for usage-limit/quota evidence. Account selection
  reads this field; account health keeps reading `failure_class`. Do not add a
  second public classifier.
- [ ] `app/core/balancer/logic.py`: replace `candidates_remaining: int` with
  `more_candidates_possible: bool` in `failover_decision`; move the
  `non_retryable` check ahead of the candidate check; add
  `MAX_ACCOUNT_ATTEMPTS_CEILING` beside `BURST_SAME_ACCOUNT_MAX_RETRIES` with
  the same "not an operator knob" comment. Keep `candidates_remaining` as a
  deprecated keyword shim for one release.
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
  `account_exhaustion` is `False` for a capacity message under a rate-limit code
  and `True` for a usage-limit message; assert `is_upstream_burst_rejection` is
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
