# Design: Estimated API-Key Usage Share

## Decision

Use a live proportional estimate rather than a persisted attribution ledger.

For each account `a` in the key's configured pool:

```text
used_credits(a) = capacity_credits(a) * used_percent(a) / 100
key_share(a) = key_demand(a) / all_proxy_demand(a)
attributed_credits(a) = used_credits(a) * key_share(a)
```

The decision compares:

```text
estimated_key_used_credits = sum(attributed_credits(a))
key_allowance_credits = configured_percent / 100 * sum(capacity_credits(a))
```

Equality denies. A zero demand denominator attributes zero credits for that account.

## Policy snapshot, not a second runtime

`ApiKeysService` builds the estimate while producing the existing immutable `ApiKeyData` policy snapshot:

- HTTP authentication receives the estimate through the existing API-key cache.
- Direct WebSocket refreshes the key policy for each new client turn.
- Dashboard list/detail reads may omit the runtime estimate; only the configured percentage is part of their stable entity contract.

The proxy never introduces another estimate cache, attribution worker, or mutable per-key runtime. HTTP policy snapshots use the existing 60-second API-key cache, matching the upstream usage-refresh cadence; direct WebSocket rebuilds the snapshot for each client turn. A request and its failover attempts use the same `ApiKeyData` snapshot.

## Pool and quota window

The estimate uses the API key's account-assignment scope. A scoped key uses its assigned accounts; an unscoped key uses all non-paused, non-deactivated, non-deletion-pending accounts visible to API-key policy loading. This is intentionally a pool-level approximation, not a model-by-model entitlement calculation.

Each account is evaluated independently against its canonical current long window:

- paid plans: secondary/weekly, including a weekly window explicitly reported in the primary slot;
- monthly-only plans: monthly.

A monthly-capacity plan never falls back to an ordinary lingering secondary row. A later weekly-duration quota explicitly reported in the primary slot becomes the current canonical long window instead. The demand interval starts at `reset_at - window_minutes` and ends at the contributing sample's `recorded_at`, so a reset restarts both observed usage and attribution without a usage-epoch table, while later traffic cannot inherit earlier usage. Adding, removing, pausing, reassigning, changing the plan of an account, or moving it from `active` to `reauth_required` evicts affected cached policy snapshots through the existing account-routing or API-key invalidation event, so the next snapshot uses the changed capacity, attribution inputs, and stored-token routing-expiry boundary on every replica.

## Demand weighting

Reuse the quota planner's existing demand-unit formula as a relative weight. The units do not need to equal upstream credits because only `key / total` is used.

The existing quarter-hour demand rollup already retains account and API-key dimensions and survives raw request-log retention. One aggregate query combines its folded segment with the exact raw complement under the fold watermark. Each account's raw complement is capped at its contributing usage sample's recording time, so later or future-dated request logs cannot inflate either side of that sample's estimate. The account/window lookup is backed by `(account_id, slot_epoch)`; PostgreSQL builds that index concurrently so enabling the policy does not block writes to the retained rollup.

- Unkeyed subscription-backed generation traffic remains in the denominator and is therefore unattributed.
- Internal warm-up traffic remains in the denominator but never enters a key's numerator, even when it carries that key.
- Attribution uses the persisted model field as a positive generation invariant: ordinary nonempty generation models count; the empty metadata/control, thread-goal, and realtime model and the `files-create` / `files-finalize` sentinel models do not. Raw model-source rows are excluded separately. This avoids maintaining request-kind prefix lists.
- Folded history starts at the first complete quarter-hour slot on or after the exact quota-window boundary. While raw rows remain, the leading partial slot, trailing sample-time slot, and post-watermark tail are read raw, so neither pre-window nor post-sample traffic is charged and the folded/raw union is exact. After raw retention removes that leading edge, the estimate may undercount by at most one partial quarter-hour slot.

## Admission placement

HTTP quota-consuming surfaces enforce at their public route boundary after model-source routing and provisional fixed-limit reservation, but before subscription dispatch:

- Responses/Chat Completions, including image work routed through Responses;
- compact;
- subscription transcription.

This also covers HTTP-bridge session reuse: the public origin makes the decision before forwarding, and the owner does not repeat it for the signed request. Metadata, Codex control, thread-goal, file-control, model-source, and unrelated work bypass the guard.

Direct Responses WebSockets rebuild the API-key snapshot for each fresh client turn. Model-source fallback and continuity routing run first. The turn is then checked once at the actual subscription boundary: before opening a new upstream connection, or before registering a turn that reuses the existing subscription socket. Reattach is explicitly exempt. Bounded failover and transparent replay inherit the same admission and create no additional charge or reservation.

Fixed token/USD limits remain independent. These routes may create a provisional fixed-limit reservation before usage-share admission; if the guard refuses locally, the existing pre-dispatch cleanup releases that reservation. A reused direct-WebSocket socket does not make the refusal account-owned: locally refused work is logged without an account so it cannot feed either side of the next proportional estimate.

A request rechecks the immutable snapshot's effective expiry at the admission boundary. If a reset, evidence-freshness, or routing boundary elapsed after authentication, that stale snapshot fails open rather than issuing a false denial; the next policy load rebuilds it through the existing cache path. The denial envelope reports the earliest contributing account reset as `resets_at`. Because account windows reset independently, this is a reevaluation hint rather than a guaranteed unblock time.

## Evidence and approximation

Every account contributing capacity needs a known capacity and a fresh current long-window sample carrying `used_percent`, `reset_at`, and the canonical `window_minutes` for the resolved weekly or monthly slot. A cached complete estimate expires at the earliest of its first quota reset, its first evidence-freshness boundary, and any known stored-token expiry for a routable `reauth_required` account, so the authentication cache cannot extend stale evidence or routing capacity. A noncanonical duration fails open rather than pairing one window's percentage with another window's capacity or widening the attribution scan. The derived window start cannot be later than the sample's own recording time. Future-dated samples are not current evidence; evaluation time is captured after the usage reads so a sample committed during policy loading is still valid. If any required evidence is missing, stale, elapsed, future-dated, or otherwise incomplete, the policy snapshot records the affected account ids instead of an estimate. Admission then succeeds and invokes the existing debounced/singleflight `UsageUpdater.request_refresh(account_id)` path for at most one affected account. The always-on staggered scheduler covers the remainder; one request never fans out an upstream refresh across the pool. Creating or scheduling that wake-up is best-effort; a local scheduler failure is logged and the already fail-open request remains admitted.

This is deliberately a soft, lagging limit:

- settled request logs and periodic upstream snapshots lag live work;
- concurrent requests can overshoot;
- direct account use outside Codex-LB is not separately observable and can distort proportional attribution;
- request demand is a proxy for quota pressure, not an exact upstream charge.

Those limitations are preferable to a new allocation ledger and distributed reservation protocol for a rough operator guard.

## Rejected alternatives

- **Pool usage ceiling:** blocks keys that consumed nothing.
- **Snapshot-delta attribution ledger:** requires usage epochs, allocation jobs, reconciliation, and an unattributed ledger while upstream still provides no exact per-request charge.
- **Fixed token conversion:** repeats the original problem; conversion is model- and upstream-policy-dependent and pool capacity changes over time.
