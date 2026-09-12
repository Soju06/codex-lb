## Why

Fixes #2163 (documented residual of #2150, follow-up to #2127).

`hard_affinity_saturated` means "the resolved hard `CODEX_SESSION` owner is not
selectable". Two different causes reach that one code, and the callers' short
owner-recovery wait (`_HARD_AFFINITY_RECOVERY_SLEEP_SECONDS`, 2s per attempt)
only helps one of them:

- the owner is briefly unavailable (cap, health backoff, status transition) —
  it may recover inside the wait, which is why the wait exists;
- the caller passed the owner in its own `exclude_account_ids` — the exclusion
  filter drops it from the pool before hard ownership narrows selection to it,
  so no amount of waiting can produce a candidate, and a resolved hard row
  never spills to another account.

Two HTTP bridge replays still exclude the account they are moving off (both
deliberately left at `main` parity by #2150): the **created-only** pre-created
replay (`fresh_hard_request_account_switch_allowed`) and the
**model-fallback** replay (`_ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE`). On a
native Codex session whose affinity resolves a **raw legacy** hard
`CODEX_SESSION` row — the un-namespaced compatibility row written before
v1.22.0 / #1382, consulted through `legacy_selection_key`, which never ages
out — that owner is the only account selection may return. The reconnect loop
therefore slept 2s per attempt until the bridge request budget
(`http_responses_session_bridge_request_budget_seconds`, 7200s) was spent
(~3600 attempts, two hours of `codex.keepalive`) before surfacing the failure.

Measured on the pre-fix tree with the budget bounded to 15s and the recovery
sleep to 50ms (`tests/integration/test_http_responses_bridge.py`): **294**
saturated re-selections over 15.14s (created-only) and **295** over 15.10s
(model-fallback). After the change: **1** re-selection, 0.36s / 0.29s.

#2150 rejected the only cheap cap available to the caller — "the exclusion set
repeated unchanged" — because it cannot distinguish the two causes and would
also defeat the legitimate wait. Only the selector knows which account the row
resolved to.

## What Changes

- **The selector reports the distinction.** `run_sticky_selection_path`
  computes `_hard_affinity_owner_excluded_by_caller`: true only when the
  failure is `hard_affinity_saturated` **and** the resolved sticky owner is one
  of the caller's own `exclude_account_ids`. It travels as
  `StickySelectionOutcome.hard_affinity_owner_excluded` →
  `AccountSelection.hard_affinity_owner_excluded`. The flag is a boolean, not
  the owner id, so no new surface has to redact an account id, and it answers
  exactly the question every caller asks.
- **Callers refuse the futile wait.**
  `_account_selection_recovery_sleep_seconds` returns `None` for such a
  selection, so `_sleep_for_account_selection_recovery` returns `False` without
  sleeping once and every transport (HTTP bridge reconnect and creation, direct
  WebSocket, SSE retry) fails closed on its first saturated re-selection
  instead of spinning to its request budget. One guard, shared by all
  surfaces, so the bridge and the WebSocket path cannot drift.
- **Nothing about selection changes.** No exclusion is dropped, no row is
  rebound or deleted, no alternate account is served, no error code, status or
  payload changes. Only the delay before the failure main already produced.

## Why not the same-owner retry (rejected)

The issue's other direction — mirror the SSE path's
`hard_affinity_same_owner_retry` and reconnect the created-only /
model-fallback replay on the owner instead of excluding it — was rejected:

- The pre-selection predicate available to the retry
  (`_affinity_may_resolve_hard_owner`) is true for **every** `CODEX_SESSION`
  affinity, not only the ones that really resolve a raw legacy row. Using it
  here would disable the fresh-hard-request account switch for all native
  sessions, including the common case where the switch is the intended
  recovery from a dying account.
- Deciding it late, from this change's selector evidence, is worse: the retry
  path already cleared the session's `x-codex-turn-state` (and the session
  turn-state fields) because the account was excluded, so a resurrected
  same-owner reconnect would hand the owner a handshake stripped of the turn
  state #2150 went out of its way to preserve.
- For the model-fallback shape retrying the owner is futile by construction:
  it just rejected the requested model.
- It also needs a once-only flag inside the selection loop of
  `http_bridge/mixin.py`, which is at its architecture line ceiling.

Failing closed fast is `main`-parity-or-better: the wrong account is never
served, the hard-affinity contract is untouched, and the client's own retry
reaches the owner with no exclusion in seconds instead of hours. It is also
the behaviour `main` already has for the neighbouring shape — a 1011 close on
a hard key binds the reconnect to the session's account and
`_require_http_bridge_bound_account_not_excluded` fails it closed immediately
when the request excluded that account.

## Impact

- Specs: `sticky-session-operations` (ADDED: self-inflicted hard-affinity
  saturation is not waited on).
- Code: `_load_balancer/sticky_selection.py`, `load_balancer.py`,
  `_service/support.py`. No migration, no new setting, no dashboard change, no
  new metric.
- Tests: `tests/unit/test_load_balancer_concurrency.py` (3),
  `tests/unit/test_proxy_utils.py` (1),
  `tests/unit/test_proxy_http_bridge.py` (2),
  `tests/integration/test_http_responses_bridge.py` (2).
- Known limitation: a self-excluded hard owner that becomes *unavailable*
  during the wait could, on `main`, have had its raw row tombstoned by the
  legacy-owner abandonment path on a later attempt and then spilled. That
  lottery (median wait unbounded, up to 7200s) is now reached by the client's
  next request instead.
