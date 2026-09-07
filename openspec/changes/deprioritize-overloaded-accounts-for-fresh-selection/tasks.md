## 1. Track overload rejections independently of the generic error counters

- [x] 1.1 Add `overload_rejections`, `overload_backoff_until`, `overload_backoff_level`, `overload_last_trip_at` to `RuntimeState` (replica-local, never persisted).
- [x] 1.2 Add `app/modules/proxy/_load_balancer/overload_backoff.py` with the window (3 rejections in 120 s), bounded exponential deadline (60 s base, 600 s cap, never shortened by a later trip), level decay after 1800 s, and `record_upstream_overload` taking the balancer's per-account lock.
- [x] 1.3 Feed the window from `_handle_stream_error` after the transient penalty for `server_is_overloaded` / `overloaded_error`; keep classification, failover, and the client response unchanged.

## 2. Deprioritize on fresh selection only

- [x] 2.1 Unbound path: select from the overload-free candidates first, then from the untouched cap-filtered pool when the strategy selects none; keep cap detection on the cap-filtered pool.
- [x] 2.2 Sticky path: apply the same two-pass pick where a NEW account is chosen for a key (fresh binding, reallocation, fallback); pinned-owner paths untouched. `load_balancer.py` forwards its runtime map.
- [x] 2.3 Retry loop: HTTP-status failures keep an overload payload code instead of collapsing to `server_error`.
- [x] 2.4 Probe reservation (unbound and sticky fresh binding) runs over the same pool the selection used.

## 3. Verification

- [x] 3.1 Unit: trip only on the third in-window rejection, stale rejections pruned, exponential growth with cap, deadline never shortened, level decay, soft filter semantics (drops only while others remain).
- [x] 3.2 Unit: `record_upstream_overload` writes runtime state and logs the engagement; `_handle_stream_error` feeds the window for overload codes only and still records the generic transient error; the level saturates (no overflow at any level).
- [x] 3.3 Routing surface: `LoadBalancer.select_account()` skips a backed-off account while a healthy sibling exists, and still selects it when every sibling is in generic error backoff; `LoadBalancer._select_with_stickiness` binds a fresh key away from the backed-off account, keeps an established owner, and falls back when no alternative exists; retry code preservation; probe reservation follows the overload-free pool.
- [x] 3.4 Run `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, `scripts/check_proxy_architecture.py`, and the unit suite.
