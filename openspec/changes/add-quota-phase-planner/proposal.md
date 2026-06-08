## Why

Quota planning needs to make phase-aware routing and optional prewarm decisions
without silently burning foreground quota or hiding operator-visible state.

## What Changes

- Add audit-first quota phase planner settings, forecast, decisions, warm-now,
  and cancellation APIs.
- Record planner decisions with explicit lifecycle status and parsed audit
  details for the dashboard.
- Gate synthetic warmup traffic behind operator settings, usage evidence, daily
  budgets, and warmup-effect evidence.
- Claim planned warmup decisions as `executing` before sending synthetic traffic
  so concurrent workers cannot double-execute the same decision.

## Impact

- Fresh installs remain non-invasive and do not send synthetic traffic.
- Operators can inspect and cancel queued planner decisions.
- Synthetic warmup outcomes are auditable, and in-flight warmups cannot be
  canceled or overwritten by stale finalizers.
