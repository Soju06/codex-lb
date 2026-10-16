# Add Estimated Per-Key Usage-Share Limits

## Why

Fixed token and dollar limits do not track a changing ChatGPT account pool. Accounts reset independently and may be added, removed, paused, or reassigned, so a fixed counter quickly stops representing the intended share of available subscription quota.

A pool-wide cutoff is also wrong: it can block a key that consumed nothing merely because other keys used the pool. The useful policy is a per-key allocation whose budget scales with that key's account pool and whose usage estimate is attributable to that key.

OpenAI does not expose per-request subscription quota charges. Codex-LB can still make a deliberately rough, useful estimate from data it already owns: current account quota usage supplies the amount consumed, while request-demand rollups supply each API key's proportional share of that consumption.

## What Changes

- API keys gain an optional integer `usage_share_percent` (`usageSharePercent` on the wire), from 1 through 100. `null` disables the policy.
- When Codex-LB loads an authenticated key's policy, it estimates that key's consumption across its assigned account pool, or the eligible global pool when the key is unscoped.
- For each account, estimated key consumption is the account's currently used long-window credits multiplied by the key's share of tracked demand in that same quota window.
- The key's allowance is its configured percentage of the pool's normalized long-window capacity. Subscription-backed generation work is denied when estimated consumption is at or above that allowance.
- Model-source, file, metadata/control, thread-goal, and realtime traffic bypasses the guard and attribution. Reattach bypasses the guard; retries use the same immutable API-key policy snapshot and create no additional accounting.
- Missing or stale upstream usage evidence fails open and schedules at most one immediate refresh through the existing per-account-debounced, singleflight path; the staggered scheduler covers the rest. The limit is intentionally a lagging soft estimate; it adds no attribution ledger or reservation protocol.
- The dashboard create/edit forms expose the setting as **Estimated pool allocation (%)** and explain the approximation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: persist, validate, serialize, and project the optional percentage policy and its current estimate.
- `proxy-admission-control`: enforce the estimated allocation once at each quota-consuming subscription request boundary.
- `usage-refresh-policy`: stale evidence triggers a coalesced refresh and does not falsely deny a key.
- `frontend-architecture`: manage and display the configured adaptive percentage separately from fixed counters.

## Impact

- One nullable column on `api_keys`; existing keys remain unrestricted.
- One aggregate read over the existing quarter-hour demand rollup plus its raw tail; no attribution table, epoch table, background allocation job, or token-to-quota conversion.
- HTTP uses the existing 60-second API-key policy cache; direct WebSocket rebuilds its policy snapshot per client turn. There is no second estimate cache.
- Admission behavior changes only for keys on which an operator explicitly configures the new field.
