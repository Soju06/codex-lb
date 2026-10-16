# Tasks

## 0. Decide

- [x] 0.1 Settle the lease-pressure unit: percentage points per full-size lease (see `context.md`, "Decision").

## 1. Pressure units

- [x] 1.1 `_state_from_account` (`app/modules/proxy/load_balancer.py`): replace the leased-token term with `lease_token_weight x leased_tokens / max_lease_estimate` percentage points, where the maximum estimate is the existing 8,192 + 2,048 token budget; stop dividing by `capacity_credits`.
- [x] 1.2 Clamp runtime pressure so that an account with persisted window usage below 100 never reaches 100 effective usage from pressure alone (applies to `effective_used_percent` and `effective_secondary_used_percent`).
- [x] 1.3 Check the other readers of the pressure-adjusted fields (sticky budget gates, `_state_above_budget_threshold`, health tiers, quota planner state builds) for behavior that changes because of the clamp, and note any intended change in `context.md`.

## 2. Relative-availability fallback

- [x] 2.1 `_select_relative_availability` (`app/core/balancer/logic.py`): when no weighted candidates remain, rank by persisted (pressure-free) secondary then primary usage before recency/id/seed. Carry persisted usage on `AccountState` if it is not already available there.
- [x] 2.2 `_seeded_least_used`: seed only among accounts tied on the persisted-usage ranking.

## 3. Verification

- [x] 3.1 Integration test at the routing path: two Plus accounts with persisted weekly usage of 95 and 38 percent and one stream lease each; route an unbound request through the load balancer and assert the 38 percent account is selected.
- [x] 3.2 Integration test: a `codex_session` sticky thread on the 95 percent account, with the secondary budget threshold at 95, is reallocated to the 38 percent account while both hold a stream lease.
- [x] 3.3 Regression suites for the balancer, sticky selection and routing tunables (targeted files; see the PR for counts).
- [x] 3.4 `uv run ruff check`, `uv run ruff format --check`, `make lint`, `openspec validate fix-lease-pressure-credit-units --strict`.
