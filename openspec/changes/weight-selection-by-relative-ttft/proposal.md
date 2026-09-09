# Weight Fresh Weighted Selection by Fleet-Relative First-Token Latency

## Why

The balancer has no latency signal. The weighted strategies (`capacity_weighted`, `relative_availability`) draw by remaining credits times the error-rate multiplier; everything else that shapes a fresh pick is a hard filter (health tier, routing policy, earlier-reset bucket, quota-planner cost, overload backoff, in-flight pressure). On the production pool a cohort of accounts answers the same small, low-effort prompts noticeably later than its siblings (first-token p50 2.4-2.9 s versus 1.7-1.9 s, driven by upstream reasoning on ~40% of their turns) yet receives its full credit-proportional share of fresh bindings, and every conversation that lands there pays the slower cohort for its whole sticky lifetime.

The only operator recourse today is `routing_policy=preserve` on the slow accounts: a hard exclusion that idles their capacity, cliff-switches traffic once the fast accounts exhaust, and goes stale as cohort membership drifts.

## What Changes

- **Replica-local TTFT samples.** The request-log funnel records the first-token latency of successful `normal` turns with fewer than 20 000 uncached input tokens (input minus cached prefix, so long sticky sessions keep sampling), reasoning effort absent/`minimal`/`low`, no response-create-gate or bridge-queue wait, and a single upstream send with no account-capacity wait, into a bounded per-account ring (1 h window, 64 samples). `warmup`, `compaction` and `realtime_live` rows, error rows, queued rows and WebSocket/bridge rows that retried `response.create` or waited for account capacity are never sampled: their first-token latency is measured from the request start, so it would contain the failed attempt plus the recovery sleep and, after an account switch, be charged to the destination account. Nothing is persisted.
- **Fleet-relative soft weight.** When at least 3 accounts hold at least 8 samples, each account's upper-trimmed mean (slowest decile dropped) is compared with the fleet median of those estimates; an account slower than the fleet by more than 15% has its draw weight multiplied by `max(0.5, fleet / account)`. The multiplier compounds with the error-rate multiplier and is consulted only by the weighted draws: sticky owners, hard continuity, deterministic probes and deterministic strategies are unaffected, and no account is ever excluded.
- **Transition log.** One INFO line `ttft_cohort_weight_change` when an account's multiplier crosses 1.0 or moves by more than 0.1, carrying the multiplier, both estimates, sample and pool counts and no account identifiers (the state build has no redaction flag).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: weighted strategies discount relatively slow first-token latency; the replica-local transient-signal list gains recent first-token latency samples.

## Impact

- New `app/modules/proxy/_load_balancer/ttft_cohort.py` (constants, sampler, estimator, weight application). `RuntimeState` gains `ttft_samples` and `ttft_weight`; the detached opportunistic snapshot copies the samples. `_write_request_log` calls the sampler after its Prometheus phase observations; `_build_states` applies the weights after building states (+2 lines in `load_balancer.py`, ceiling 3021).
- Zero-config: no new `CODEX_LB_*` setting, no schema, migration, API or dashboard change; Settings stays at the 130 ratchet. The feature is fleet-relative and floored, so it is neutral on thin evidence and on a uniformly slow fleet; that is why no toggle is shipped. The error-rate precedent's toggle exists because that discount can reach 0.05; this one never drops below 0.5.
- Behavior change for weighted strategies only: the fast cohort's fresh share rises from its credit share to at most about 1.6x that share relative to a floored account; established owners never move because of it.
- Accounts on other paths: HTTP-upstream rows never carry gate/bridge wait values and measure TTFT per attempt, so the queued-wait and retry filters only drop WebSocket/bridge rows that actually waited or retried locally. The WebSocket finalizer passes a new `upstream_retried` funnel argument (`response_create_attempt_count > 1` or an account-capacity wait was started); it is not persisted.
- Observe-only opportunistic admission builds states on a detached runtime snapshot; that build applies the same multiplier but does not emit the transition log (`log_weight_transitions=False`), because the `ttft_weight` written to the snapshot is discarded and the next live build would repeat the line.

## Known Limitations

- Replica-local by design; each replica converges on its own samples (existing requirement, now enumerating this signal).
- Sample volume: an account needs 8 eligible turns per hour and 3 accounts need evidence; quiet hours are neutral by design. If production shows the weight stays neutral, widening the window or the effort filter is a module-constant change, not a setting.
- `prefer_earlier_reset` still narrows to the earliest-reset bucket first; the weight acts within that bucket.
- The sampling slice has no model dimension: an account whose API key or model catalog steers it a heavier model mix can be discounted for its mix rather than its health. The uncached-input and effort filters bound this; a per-model-class slice is a follow-up if production shows it matters.
- No Prometheus gauge in this change; the transition log and `request_logs` are sufficient to judge whether the weight moves. A gauge is a follow-up once it does.
