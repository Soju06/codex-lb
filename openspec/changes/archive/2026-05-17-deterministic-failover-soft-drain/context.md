# Context: Deterministic Failover & Soft Drain

## Purpose

Make codex-lb's rate-limit and quota handling **deterministic and proactive** so that:
1. Clients almost never see upstream 429/quota errors when healthy accounts remain.
2. Accounts approaching their limits are drained before hard failure.
3. All three transports (SSE streaming, compact HTTP, WebSocket) follow the same failover policy.

## Key Design Decisions

### Why runtime-only state for soft drain (no DB schema change)?

Soft drain is **transient by nature**. It reflects real-time signals (usage %, recent errors, latency) that change every few seconds. On process restart, all accounts reset to HEALTHY and re-evaluate on the first selection — the usage data from DB provides enough signal to re-enter DRAINING within one selection cycle. This avoids:
- Schema migrations for a volatile field
- Cross-process consistency concerns for a fundamentally local optimisation

### Why `health_tier: int` instead of a new enum?

Using `int` (0=HEALTHY, 1=DRAINING, 2=PROBING) allows the tier to participate directly in sort keys without branching. The `select_account()` modification is a simple list partition rather than a new code path. The values are ordered by preference: lower is better.

### Why a single `failover_decision()` pure function?

Today, failover logic is scattered across `_stream_with_retry` (3 different exception handlers), `compact_responses` (inline checks), and WebSocket (no retry at all). A pure function with signature `(failure_class, downstream_visible, candidates_remaining) → action` makes the policy:
- **Testable**: table-driven unit tests cover all combinations.
- **Auditable**: one place to review the complete policy.
- **Deterministic**: same inputs always produce same output.

### The "downstream_visible" commit boundary

This is the single most important invariant:

| Transport | Visible when... |
|---|---|
| SSE streaming | Any SSE frame has been `yield`ed |
| Compact HTTP | Response headers/body write started |
| WebSocket | Any application frame relayed downstream |

Once visible, transparent failover is forbidden. This prevents:
- Duplicate upstream execution (billing, side effects)
- Partial/corrupted response streams
- Session state inconsistency

### Drain thresholds rationale

| Threshold | Value | Why |
|---|---|---|
| Primary usage | 85% | Leaves ~15% headroom for in-flight requests before hard 429 |
| Secondary usage | 90% | Secondary (7-day) quotas are larger; 10% buffer is sufficient |
| Error spike | 2 errors in 60s | Two transient errors in a minute suggests instability, not bad luck |
| Probe quiet period | 60s | Long enough to let upstream recover; short enough to not waste capacity |
| Probe success streak | 3 | Three consecutive successes gives reasonable confidence of recovery |

All thresholds are configurable via `Settings` (env vars).

### Sticky session interaction

- **HEALTHY**: Normal sticky behaviour.
- **DRAINING**: Existing `CODEX_SESSION` stickies continue (session continuity is critical for Codex). New `PROMPT_CACHE` / `STICKY_THREAD` stickies are **not created** — they go to a healthier account.
- **PROBING**: No new stickies. Only receives non-sticky traffic for recovery validation.

## Failure Modes

### All accounts simultaneously DRAINING
The selection fallback chain is `healthy or probing or draining or available`. DRAINING accounts are **never hard-blocked** — they're deprioritised. If every account is DRAINING, the capacity-weighted algorithm still selects the best one. The system degrades gracefully rather than refusing traffic.

### Classifier misidentifies error type
The classifier uses the same `_ACCOUNT_RECOVERY_RETRY_CODES` set that the existing `_should_retry_stream_error` uses. No new error code interpretation is introduced. The classifier strictly maps known codes; unknown codes default to `non_retryable` (safe fallback — surfaces to client rather than risking duplicate execution).

### Failover increases TTFB
Each failover attempt adds ~10ms (account selection + connect). With max 3 attempts, worst case is ~30ms overhead — negligible compared to typical upstream response latency (500ms–30s for reasoning models). Metrics track TTFB with and without failover for monitoring.

## Example: Streaming connect-phase 429 → transparent failover

```
Client → POST /v1/responses (SSE)
  codex-lb selects Account A (HEALTHY, 82% used)
  → upstream returns HTTP 429 {"error": {"code": "rate_limit_exceeded"}}
  → classify_upstream_failure → failure_class=rate_limit, phase=connect
  → failover_decision(downstream_visible=False, candidates_remaining=2) → failover_next
  → mark_rate_limit(Account A) → Account A becomes RATE_LIMITED
  → select Account B (HEALTHY, 45% used)
  → upstream returns 200 OK, SSE stream begins
  → yield first SSE event to client
Client receives normal stream — never saw the 429
```
