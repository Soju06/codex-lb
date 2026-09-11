# Tasks

## 1. Deterministic substitute

- [x] 1.1 `overload_backoff.deterministic_isolation_substitute(pool, *, sticky_key, owner_account_id)`: pure helper returning the sticky key's blake2b index into the pool sorted by `account_id` with the owner removed, or `None` when no sibling remains. Order-independent so replicas observing the same pool converge; documents that differing per-replica pools cap rather than eliminate fan-out, and that callers must still put the pick through the real selector.
- [x] 1.2 Module docstring: the isolation stage now describes a request-local release, states the measured accounts-per-conversation baseline that motivated it, and records that substitute stability is the correctness condition.

## 2. Selection

- [x] 2.1 `_run_select_with_stickiness`: drop the `and not owner_overload_isolated` suppressor from the bare-session cap-spillover `preserve_existing_mapping` expression and from the soft-TTL-owner preservation block, so an owner that is both isolated and request-locally unavailable preserves instead of being stranded on the sibling. The `owner_overload_isolated` cap-eligibility carve-out for the substitute is unchanged.
- [x] 2.2 `_select_with_stickiness`: gate the isolation reroute on `pinned.status in _RECOVERABLE_STATUSES` so a `paused`/`deactivated` owner falls through to the unchanged rebind path and is released on that turn.
- [x] 2.3 `_select_with_stickiness`: prefer `deterministic_isolation_substitute` over the weighted pool draw, validating it by running the real selector on the single candidate and falling back to the pool pick when it is rejected. The secondary-budget filter for a budget-pressured owner still applies to the substitute.
- [x] 2.4 `_select_with_stickiness`: set `overload_reroute_request_local` alongside `reallocate_sticky` and, in the mutation block, take `persist_fallback = False` for that case instead of `_StickyMutation(account_id=None)`. `reallocate_sticky` is still set so the pinned-owner return and the rate-limit grace retry are skipped as before.
- [x] 2.5 Capture the caller's `reallocate_sticky` before the isolation branch reuses that local, and take the request-local path only when the caller did not ask for reallocation. An explicit reallocation during isolation still retires the mapping.
- [x] 2.6 `sticky_owner_overload_isolation_reroute` carries `mapping=retained|rebound` and `substitute=deterministic|weighted`; still no account identifiers.

## 3. Verification

- [x] 3.1 `tests/unit/test_overload_backoff.py`: isolated soft owner (all three soft kinds) is served by a sibling with **no mutation**; the same thread keeps exactly one substitute over 24 turns while distinct threads spread over the siblings; a `deactivated` isolated owner is rebound on the next turn (the availability bound); a hard `codex_session` owner under isolation is neither released nor rewritten; the diagnostic prints `mapping=retained substitute=deterministic` and no account ids; the helper is stable, order-independent and returns `None` without a sibling.
- [x] 3.2 Inverted rebind assertions: `..._is_rerouted_and_rebound` → `..._is_served_by_a_substitute_without_rebinding`; `..._is_rebound_instead_of_request_local_spillover` → `..._keeps_its_request_local_spillover`; the cap-off bare-session test's second half and the budget-pressured test now assert no upsert; `tests/unit/test_load_balancer_concurrency.py::test_isolated_and_capped_prompt_cache_owner_is_rebound` → `..._keeps_its_mapping`.
- [x] 3.3 `tests/unit/test_load_balancer_concurrency.py`: an owner that is both isolated and excluded by this request's retry loop keeps its mapping (isolation must not convert a request-local spillover into a rebind); the security-work-excluded owner is still rebound.
- [x] 3.4 Unchanged and green: the pool-helper contract, soft-backoff-keeps-owner, kept-when-nothing-selectable, expired-isolation and probe-reservation-sees-`effective_states` cases; `tests/unit/test_select_with_stickiness.py` pinned-owner retention.
- [x] 3.5 Regression: `tests/unit/test_overload_backoff.py tests/unit/test_load_balancer_concurrency.py tests/unit/test_select_with_stickiness.py tests/unit/test_proxy_soft_sticky_spillover.py tests/unit/test_proxy_utils.py tests/unit/test_routing_tunables.py tests/unit/test_load_balancer_contract.py`, plus the full unit suite.
- [x] 3.6 Guards: `ruff check`, `ruff format --check`, the repository architecture/simplicity checks, and `openspec validate retain-warm-sticky-owners-through-isolation --strict`.
