# Add congestion-aware per-API-key fair-share stream admission

## Why

Stream slots are allocated across API keys first-come-first-served. The per-account stream cap protects individual ChatGPT accounts from upstream 429s, but nothing above it protects the *pool*: one bursty key can hold every slot and starve every other key. A production deployment observed exactly this (issue #1535): one agentic fan-out key held ~30-36 concurrent streams (91 live upstream streams pool-wide, ~87% of the 104-slot capacity of a 13-account pool), one account additionally hit a genuine upstream 429, and from then on `account_stream_cap` filtered every candidate — selection degraded to `no_accounts` and **every** key, including interactive keys holding 0-2 streams, received sustained 503 bursts (20-60% of requests per minute) while client retries amplified the load.

A static per-key concurrency cap is the wrong fix: it wastes idle capacity in the common single-tenant-burst case that operators explicitly want to allow. The requirement is congestion-triggered max-min fairness — unlimited when the pool is loose, throttle the heaviest keys first when it is tight. The archived `2026-06-02-stabilize-responses-concurrency-streaming` change explicitly deferred cross-key fairness; #1414 requests the sibling bulkhead concern for media traffic; #1246 covers the related pool-exhausted error semantics; #1354 is an amplifier of the same exhaustion.

## What Changes

- New pure module `app/modules/proxy/fair_share.py`: integer-math max-min fair-share decision. Pool capacity `C` = candidate-account count x per-account effective stream slots (`max(1, stream_limit - stream_reserve_slots)`); pool in-flight `T` = sum of candidate accounts' in-flight stream leases. The gate is active only when a configured congestion threshold is set and `T*100 >= C*threshold_pct`. While congested, a key is admitted only when `key_inflight + 1 <= max(2, C // active_keys)` (`active_keys` = keys holding at least one candidate-account stream lease, requester counted exactly once). Keys holding fewer than two streams therefore always admit — light interactive keys are starvation-proof — while over-share keys cannot reacquire freed slots until they drop back under their share.
- Stream leases are attributed to the requesting API key: `AccountLease` gains `api_key_id`, `RuntimeState` gains a per-key in-flight map maintained on acquire/release/stale-reclaim under the existing runtime lock. Candidate-set scoping means account-scoped keys are measured against the pool they can actually use.
- The gate is evaluated inside both selection paths' runtime-lock sections (sticky filter phase plus a commit-phase re-check, mirroring the existing account-cap re-check; unbound path is single-lock and needs no re-check). Denials return the new stable local-overload reason `api_key_stream_fair_share` and reuse the existing account-capacity machinery unchanged: park-and-retry with `waiting_for_account_capacity` keepalives, then 429 `rate_limit_error` with `Retry-After` on budget exhaustion. No new transport code.
- Bypasses: keyless requests (their streams still count toward `T`), reattach-stage selection (resuming an existing in-flight response), non-stream leases, and unlimited caps (`stream_limit <= 0`).
- One new setting, default off: `proxy_api_key_fair_share_congestion_threshold_pct` (env `CODEX_LB_PROXY_API_KEY_FAIR_SHARE_CONGESTION_THRESHOLD_PCT`, 0-100, 0 disables), with the usual nullable dashboard override (null inherits the environment) surfaced next to the per-account capacity limits in routing settings (en/ko/zh-CN). The minimum guarantee (2 streams) is a hardcoded constant, not a second setting.
- Observability: gauges `codex_lb_stream_pool_capacity` and `codex_lb_stream_pool_inflight` (no labels; the previously missing pool numerator/denominator), counter `codex_lb_api_key_fair_share_rejections_total` (no per-key label by design — cardinality), and a warning log carrying `api_key_id`, requester in-flight, fair share, `T`/`C`, and active-key count.

## Impact

- Default behavior is unchanged (threshold 0 = gate fully inactive; characterization-tested).
- With the gate enabled at e.g. 80%, the incident above degrades gracefully instead of globally: the bursty key converges to its fair share (excess requests park and retry politely), all other keys keep admitting, and no `no_accounts` 503s are produced by slot exhaustion.
- Fairness scope is per replica and per worker process — the same statistical model the per-account caps already use (partitioned caps, process-local counters); documented in the design.
- v1 deliberately excludes response-create leases, the opportunistic pre-check at the API edge, and token-weighted shares (design lists them as follow-ups).
- Affected specs: `proxy-admission-control` (gate, accounting, wait semantics), `proxy-runtime-observability` (metrics/log), `frontend-architecture` (settings field).

## Non-goals

- Exact cross-replica/cross-worker fairness through shared counters (rejected for the same hot-path reasons as the cap-partitioning change).
- Static per-key concurrency limits or request-rate limits.
- Priority tiers between keys (the existing `traffic_class` opportunistic machinery already covers deprioritization; the fair-share gate composes with it).
