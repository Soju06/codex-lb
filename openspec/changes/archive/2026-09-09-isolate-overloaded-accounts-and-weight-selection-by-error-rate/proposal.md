# Isolate Overloaded Accounts and Weight Selection by Error Rate

## Why

Since 2026-09-07 03:00 UTC upstream has been rejecting fresh admissions on a fixed subset of pool accounts with `server_is_overloaded` ("Our servers are currently overloaded") while their siblings stay clean: on the production pool 9 of 19 accounts rejected 17-44% of their requests and the other 10 rejected 0.1-0.3%, on the same egress IP, models and hours. The rejection arrives 30-100 s after dispatch (p50 33 s, p90 94 s), so every request routed to a rejected account pays that wait before failover even starts, and the pool-wide error rate went from 0.3-1.7% to 9-12%.

The soft overload backoff (#2137) only steers *fresh* selections and fresh sticky bindings away from a rejected account. Production shows that about 80% of the rejected requests reach the account through an **established soft sticky owner** (HTTP Responses sessions and Codex sessions already bound to it): every such request is a fresh upstream admission, so the pinned owner replays the rejection wait on each turn while the generic health tier never latches (warm bridge successes zero `error_count`). The backoff also caps at 600 s, so a throttle lasting hours re-admits the account every ten minutes and re-learns the same lesson.

Independently, the weighted routing strategies draw by remaining credits alone. An account failing one request in three has the same draw weight as a clean one because `error_count` is a latch, not a rate.

## What Changes

- **Isolation stage for the overload backoff.** When an account's overload backoff level reaches the isolation trip level (third trip, i.e. sustained rejection despite two deprioritizations), it is held out for `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS` (default 30 min, `0` keeps the soft backoff only) instead of the 60-600 s soft interval. While isolated, established **soft** sticky owners (`prompt_cache`, `sticky_thread`, `codex_session` mappings) are released and rebound to an overload-free account when the configured strategy can select one; a lone or fully-overloaded pool keeps the owner. Hard continuity owners (`previous_response_id`, bridge ownership, file pins, turn-state rows) are resolved before soft selection and are never moved. The backoff level no longer decays while the account is held out, so an account leaving isolation that is still rejected re-isolates on its next trip. A process-session preference for a brand-new thread is likewise skipped while the preferred account is in overload backoff and the strategy actually selects an overload-free candidate (an unselectable alternative keeps the preference).
- **Error-rate weighting for weighted strategies.** The balancer keeps a replica-local ten-minute window of upstream outcomes per account (successes and the account-attributable transient failures that reach `record_errors`; rate-limit, quota, permanent and account-neutral failures keep their own paths). `capacity_weighted` and `relative_availability` multiply each candidate's draw weight by `max(0.05, 1 - recent_error_rate)` once the window holds at least 10 outcomes. Deterministic strategies are unchanged. `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=false` disables the discount.
- New `Account overload isolation engaged` warning (account under the redaction policy) and `sticky_owner_overload_isolation_reroute` info diagnostic (sticky kind and pool size only, no account identifiers).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: overload backoff gains an isolation stage; weighted strategies discount recent error rate.
- `sticky-session-operations`: an isolated account releases its soft sticky owners while another candidate can be selected.

## Impact

- `app/modules/proxy/_load_balancer/overload_backoff.py` (isolation policy, decay-from-deadline, reroute pool helper), new `app/modules/proxy/_load_balancer/error_rate.py`, `app/modules/proxy/_load_balancer/sticky_selection.py` (pinned-owner release, process-session preference), `app/modules/proxy/_load_balancer/types.py` (two runtime fields), `app/core/balancer/logic.py` (`AccountState.selection_weight_multiplier`, applied in the two weighted draws), `app/modules/proxy/load_balancer.py` (+4 lines: outcome hooks and multiplier; stays at the 3021-line ratchet).
- Two new `CODEX_LB_*` settings (`proxy_overload_isolation_seconds`, `proxy_account_error_rate_weighting_enabled`); the settings surface ratchet moves 133 -> 135 and `docs/reference/settings.md` is regenerated. No schema, migration, API or dashboard change; all state is replica-local and never persisted.
- Zero-config behavior changes: sustained-overload accounts are isolated for 30 min and flaky accounts receive proportionally less weighted traffic. Both keep the existing invariant that a candidate is dropped only while another remains, so neither can turn usable capacity into `No available accounts`.

## Known Limitations

- Isolation and the outcome window are per replica; peers converge on their own observations (existing "replica-local health signals" requirement).
- The error-rate discount only affects strategies that draw randomly by weight. `round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain` and `single_account` are unchanged.
- An isolated account still serves hard continuity owners and warm bridge sessions; only fresh admissions and soft owners move.
- A released owner's replacement is chosen with the eligibility every fresh sticky binding already has (strategy, budget gates, stream cap; the response-create pre-filter applies only to bare-session cap spillover, as on `main`). A replacement whose response-create slots are full is handled by the existing pre-dispatch cap wait/failover, exactly like a fresh binding.
