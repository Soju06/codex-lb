## Context

Native Codex HTTP turns run `select_account` on every turn inside the per-attempt retry loop (`retry.py`, `_select_account_with_budget_compatible(..., exclude_account_ids=excluded_account_ids)`), while WebSocket turns select once per upstream socket. Both derive the same soft `prompt_cache` thread row (`affinity._thread_codex_session_affinity`, `max_age` 1800 s, `reallocate_sticky` False). `_select_with_stickiness` already keeps that row when the pinned owner is visible but unhealthy (`persist_fallback = False` for TTL kinds). It rebinds permanently when the owner is invisible (`pinned is None` → delete + persist fallback), which two request-local conditions cause on the HTTP path:

- A1 — the retry loop excluded the owner after a transient failure (code-less 429 → `upstream_error` → `retryable_transient` → `failover_next`; `account_response_create_cap`), `load_selection_inputs` drops it from `accounts`, `states` is built from `accounts` only.
- A2 — the per-account cap filter (`_filter_states_for_account_caps`) removed it before `_select_with_stickiness` ran; the existing request-local exception is gated on a bare `codex_session` key and does not cover `prompt_cache` rows.

## Decisions

1. **Generalise the existing bare-session rule; no new mechanism.** `preserve_existing_mapping` is extended for soft TTL-bounded owners; the whole request-local path (`preserve_existing_mapping_on_fallback=True` → `persist_fallback=False` → no delete → `internal_soft_affinity_spillover` → no mutation persisted) is existing code. No change to `_select_with_stickiness`'s 30-kwarg signature, no reason tag on the log (`sticky_kind` already distinguishes bare-session from prompt-cache spills).
2. **Explicit `exclude_account_ids` on the request instead of diffing pools.** `effective_continuity_owner_candidates` is captured pre-status and pre-exclusion, but it also differs from `accounts` by catalog-evidence model filtering, so "in continuity pool but not in states" cannot by itself prove "excluded by this request's retry loop". The field is defaulted; the only constructor forwards `frozenset(excluded_ids)`.
3. **One definition of "recoverable".** The excluded branch requires the owner's persisted status to be in the module constant `_RECOVERABLE_STATUSES` (`active`, `reauth_required`, `rate_limited`, `quota_exceeded`), the same set the visible-owner fallback uses, so `paused`/`deactivated` owners are still rebound.
4. **Gates that keep today's permanent-removal semantics.** `not hard_sticky` (turn-state / legacy raw rows are ownership constraints), `not reallocate_sticky` (budget, rate-limit-far, model-unsupported, WS retry and pre-dispatch connect failures still rebind), `sticky_max_age_seconds is not None` (durable no-TTL `codex_session` rows keep persisting their fallback during an outage, per "Sticky sessions are explicitly typed"), `not owner_overload_isolated` (matches "Capped and isolated owner is rebound to the spillover target"), and owner absence from `effective_continuity_owner_candidates` (deleted, out of API-key scope, or filtered by `require_security_work_authorized`, which narrows `continuity_owner_candidates` to authorized accounts) still rebinds.
5. **`not hard_sticky` is defensive, not load-bearing.** A hard row already narrows `selection_states` to the owner alone, so an excluded hard owner reaches `_select_with_stickiness` with an empty pool and surfaces `hard_affinity_saturated` whether or not the predicate fires; the gate documents the intent ("hard rows are never spillable") rather than changing an outcome, which is why no test can kill it.
6. **The spillover diagnostic honours request redaction.** `internal_soft_affinity_spillover` used to print raw account ids; it now fires on the common thread-header PROMPT_CACHE path (and for `codex_control` callers that pass `redact_sensitive_details`), so the flag is threaded into `_select_with_stickiness` (Protocol + `LoadBalancer` wrapper + core function, one defaulted kwarg) and both ids print as `<redacted>` when requested, matching the neighbouring owner-lookup log.
7. **No setting.** The settings ratchet is full (`check_settings_tiers.py`: 130/130) and the behaviour is a consistency fix with the visible-but-unhealthy TTL path, so a hardcoded predicate, not a knob.

## Exclusion-site audit (`excluded_account_ids.add` in `retry.py`, 24 sites at c38e4de15)

Under the new predicate every exclusion that does not also set `reallocate_sticky` preserves a recoverable, in-scope `prompt_cache` owner for this request only. `tests/unit/test_proxy_soft_sticky_spillover.py` drives the real retry loop into the real `LoadBalancer` for one site of each kind: the pre-visible transient failover (code-less 429 → preserved) and the account/model rejection (`reallocate_sticky=True` → rebound).

| Class | Sites (line) | Owner outcome under this change |
| --- | --- | --- |
| Transient / request-local: failover_next after a pre-visible upstream failure, transient-exhausted, response-create cap deferral, held-claim contention, dead-account slot release, post-refresh transport failure | 1912, 2036, 2341, 2366, 2470, 2554, 2619, 2747, 2845, 2993, 3016, 3121 | Preserved (request-local spill) — the intended change |
| Reallocate-flagged (`affinity = replace(affinity, reallocate_sticky=True)`): model unsupported, pre-dispatch proxy connect failure (pre- and post-refresh) | 1152, 2425, 3062 | Rebound, unchanged (`not reallocate_sticky` gate) |
| Owner-scope-changing (`require_security_work_authorized = True`) | 2315, 2610, 3202 | Rebound, unchanged: `continuity_owner_candidates` is narrowed to authorized accounts, so the owner leaves `effective_continuity_owner_candidates` |
| Permanent refresh failure (`mark_permanent_failure` → `reauth_required`) | 1877, 2717, 3229 | Preserved for this request (`reauth_required` is recoverable, per `account-routing` "Warning state preserves ownership"); the status transition itself deletes the account's mappings per "Unusable account transitions remove persistent affinity bindings", so no stale rebind is needed here |
| Verified-fresh-replay owner moves (`affinity = replace(affinity, reallocate_sticky=True)` follows each) | 794, 1566, 1751 | Rebound, unchanged: the replay payload is moved off the owner and the site requests reallocation, so the `not reallocate_sticky` gate keeps today's durable move |

## Ceilings (scripts/check_proxy_architecture.py thresholds from `proxy-architecture` spec)

`load_balancer.py` 3020/3021 (main sits at exactly 3021; this change adds 3 lines — the `exclude_account_ids` pass-through in `select_account`, span +1 against its 527 ceiling, and the `redact_sensitive_details` kwarg + forward in the `_select_with_stickiness` wrapper — and pays for them by collapsing two trailing-comma-split single-name `sticky_selection` re-export imports onto one line each, no behavior change), `service.py` 2560/2600, `http_bridge/mixin.py` 2427/2436, `streaming/mixin.py` 1098/1100 — the latter two are effectively frozen, which is why the change lives in `_load_balancer/sticky_selection.py` (uncapped).

## Deferred (separate changes)

- WS connect-retry parity: `websocket/mixin.py` `reallocate_sticky=True if is_retry` → only for non-TTL kinds.
- Same-owner bounded backoff before failover for soft/owner-bound HTTP turns in `retry.py` (amends `account-routing` "failover decision MUST remain unchanged").
- Operator levers: dashboard `openai_cache_affinity_max_age_seconds`, downstream WebSocket idle timeout.
