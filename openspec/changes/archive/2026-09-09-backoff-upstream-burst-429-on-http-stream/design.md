## Context

See `proposal.md` for the production numbers. Two facts shape the design. First, the upstream rejection is a **burst** signal: the same account succeeds within seconds, so any reaction longer than a few tens of seconds turns a hiccup into lost capacity, and any reaction that persists (status flip, `cooldown_until`, `RATE_LIMITED`) is wrong for the whole pool of replicas. Second, most affected turns are **owner-bound**: multi-turn Codex payloads carry `reasoning` items that are not account-neutral, so the request cannot legitimately move to another account and the only correct recovery is to wait briefly and try the same owner again — inside the request, because the client makes exactly one attempt.

## Goals / Non-Goals

**Goals:**

- Absorb a code-less upstream HTTP 429 inside the request for owner-bound turns, bounded by retry count and by the request budget, without committing the HTTP response status during the wait.
- Steer fresh (unbound) selection away from the rejecting account for a few seconds using the machinery the overload soft backoff already threads through `unbound_selection.py` and `sticky_selection.py`.
- Make the `Failover decision` log line report the action that is executed.
- Surface the original upstream 429, with `Retry-After`, when the bounded recovery fails.

**Non-Goals:**

- Changing `classify_upstream_failure` outputs, the stable error taxonomy, or the `_ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES` set (adding `reasoning` there would let a reasoning-bearing payload be replayed to a different account).
- Adding a `CODEX_LB_*` setting, a persisted status, a schema change, or cross-replica agreement.
- Covering the direct WebSocket and HTTP-bridge transports, which relabel a handshake 429 to 502 before this classification.

## Decisions

### Not `mark_rate_limit`, `cooldown_until`, or persisted `RATE_LIMITED`

Those paths implement `account-routing`'s rate-limit cooldown: they persist status, `reset_at` and `blocked_at`, are enforced across replicas, and honor `Retry-After` hint durations measured in minutes. A burst rejection is neither account-wide nor durable — 92% of them coexist with a success on the same account within ±3 s — so a persisted flip would bench a healthy account on every replica for far longer than the burst and would also override the dashboard status. Coded 429s (`rate_limit_exceeded`, `usage_limit_reached`) keep that path untouched because their classification (`rate_limit` / quota) returns before the new hook.

### Not a new error code or setting

The client-visible body stays the upstream body (`upstream_error`, upstream message) so nothing in the failure taxonomy, dashboards, or `tests/unit/test_failover_foundation.py` changes; the new signal is keyed on `http_status == 429` inside the `retryable_transient` branch, which also covers `server_error` rewrites and `detail`-parsed bodies. The bounds (5 s default / 30 s cap for the cooldown; 3 retries, 1 s base, 10 s cap for the same-account wait) are module constants: the `Settings.model_fields` ratchet is exactly full at 130, and the observed recovery window does not vary per deployment.

### Reuse the overload soft backoff predicate, not the isolation stage

`overload_backoff_active` is the single predicate `filter_overload_backoff_candidates` and the sticky fresh-binding paths read; making it true while `burst_backoff_until` is in the future inherits the #2137 contract with zero edits to `load_balancer.py`, `unbound_selection.py` or `sticky_selection.py`: a candidate is dropped only while another remains, the configured strategy judges the remaining pool, and it falls back to the full pool when it selects none, so the cooldown can never produce `No available accounts`. The burst deadline is a separate field that never writes the overload rejection window, `overload_backoff_until`, or `overload_isolated_until`, so a 5-30 s burst cannot escalate the overload level toward the 30-minute isolation stage, and `overload_isolation_active` stays false — established soft sticky owners are not rerouted by a burst (they are exactly the owner-bound requests fix B retries in place). The two deadlines are independent: the account is skipped for fresh selection while either is in the future.

### Owner-bound: `retry_same_account`, never `failover_next`

`failover_decision` is the one place the three transports agree on what to do after a pre-visible failure. An owner-bound request cannot select another account, so returning `failover_next` produced a log line that promised a failover the loop could not perform (the next selection returned `preferred_account_unavailable` and the saved 429 was re-raised). The new `owner_bound` input makes the decision honest: `retry_same_account` while a same-account retry is available, otherwise `surface`. Owner-boundness is derived from the loop's existing continuity inputs (payload replay binding to this account, required preferred account, file-pin owner, turn-state owner) through one shared predicate used by both the pre-visible and post-refresh blocks. Downstream-visible output always surfaces, as today; replaying after visible output is never attempted.

### Header-holding wait, not a bare sleep

`api.py::_wait_for_first_stream_probe` commits a 200/SSE response after a 0.05 s probe unless the stream signals a propagated startup wait. The same-account backoff therefore goes through `_iter_account_capacity_recovery_wait` (the mechanism the response-create cap wait already uses) with `stage="burst_backoff"`, so the HTTP headers keep being held and the surfaced 429 after exhaustion is still a real HTTP 429 rather than an error event inside a committed 200. Two details make that hold real for the population in the RCA (Codex CLI: `propagate_http_errors=True`, `enforce_openai_sdk_contract=False`): the burst wait passes `emit_keepalives=not propagate_http_errors` -- the response-create cap wait's `or not enforce_openai_sdk_contract` rule would yield a `codex.keepalive` frame as the first stream item and commit 200 for a native client, and a wait bounded to <= 10 s needs no keepalive -- and the probe's bounded post-ready window now keeps watching the wait marker, so a second rejection that parks the stream on a further wait re-arms the hold instead of expiring the window and handing off. The wait uses the `scheduler` / `clock` seams already threaded through `_stream_with_retry`; `retry.py` has zero allowance for raw `asyncio.sleep` / `time.*`.

### Delay schedule

`min(10 s, max(Retry-After, 1 s * 2^(n-1)))` for retry `n` in 1..3: 1 s, 2 s, 4 s without a hint, up to 7 s total, well inside the observed ±3 s recovery window with headroom for the p50 5 in-flight peak to drain. The upstream `Retry-After`, when present, is a floor (upstream knows its own window better than the schedule) and the 10 s cap bounds a hostile or stale header. Each wait is also bounded by `proxy._remaining_budget_seconds(deadline)`, so a request that arrives late in its budget surfaces sooner. Jitter is omitted; if added later it must go through an injectable RNG. When retries are exhausted the surfaced `ProxyResponseError` carries `Retry-After` (upstream value or `5`, the same default `LOCAL_OVERLOAD_RETRY_AFTER_SECONDS` uses) so `_stream_startup_error_response` emits the header.

### Interaction between the two fixes

On `retry_same_account` the loop engages fix A's cooldown directly (`record_upstream_burst_rejection`, replica-local runtime state, no DB write, no reservation-ordering dependency) so the very first rejection steers **other** requests away while the owner-bound request keeps waiting on its owner -- keyed or not. It deliberately does *not* record a transient error penalty per retry: `record_error` feeds the persisted `error_count`, and three or four counts within seconds would put the owner into the 30-60 s selection error backoff, making the very owner the request is bound to unselectable for its next turn (the "owner vanished" class this change fixes). The penalty is written exactly once, on the surface path, and the surfaced exception is marked so the outer terminal handler does not write it again. A movable request keeps `failover_next`, is excluded from its own re-selection as today, and fix A steers the fresh pick away from the rejecting account for the cooldown window. Keyed streams defer their transient penalty until after usage settlement (`_handle_or_defer_keyed_stream_health`); the burst cooldown is still engaged at rejection time and the queued penalty carries a `burst_cooldown_recorded` flag so the deferred `_handle_stream_error` does not re-stamp the deadline from the later clock (which would bench an account that has since succeeded).

### Exactly-once health write on surfaced pre-visible failures

Before this change a pre-visible `ProxyResponseError` that reached the surface path was penalized twice: once where the failover decision was made and once more by the outer terminal handler that catches the re-raised exception. The surfaced exception now carries a marker (`_STREAM_HEALTH_RECORDED_ATTR`) and the outer handler skips the second write. This is required for the burst contract (one penalty per request) and corrects the duplicate for the other surfaced pre-visible failures as well; coded 429s therefore call `mark_rate_limit` once instead of twice.

## Risks / Trade-offs

- An owner-bound request now spends up to ~7 s (budget-bounded) before surfacing a burst 429 that used to surface in 180 ms; that is the intended trade against the client's single attempt.
- The `retryable_transient` + HTTP 429 key is broader than the exact upstream body; a hypothetical coded 429 that classification does not recognize as rate-limit would receive the short cooldown instead of `mark_rate_limit`, which is the safer direction (5-30 s, replica-local, no status flip).
- The cooldown is replica-local; a peer that has not observed the burst may still select the account, consistent with the existing "transient balancer health signals are replica-local" requirement.
