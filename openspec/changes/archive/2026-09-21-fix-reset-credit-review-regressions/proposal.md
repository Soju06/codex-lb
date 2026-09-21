## Why

Independent review reproduced eight defects in the reset-credit reliability change, including a stopped scheduler after a successful consume and lost confirmation after a transient database failure. These must be fixed before the earlier implementation can be considered ready.

## What Changes

- Preserve scheduler work across session cleanup and schedule retries before the original expiry.
- Settle received consume receipts with bounded, cancellation-safe persistence retries and prevent duplicate manual consumes across request IDs.
- Preserve authoritative availability counts and duplicate-account identity in targeted summaries.
- Reconcile dashboard totals using backend sample eligibility and retain pending reset state across ordinary polls.
- Cover real scheduler/session/consume paths and repeat independent review.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rate-limit-reset-credits`: receipt settlement, credit-level idempotency, scheduler lifetime, expiry-aware recovery, authoritative summaries.
- `frontend-architecture`: reset reconciliation state lifetime and aggregate quota eligibility.

## Impact

Reset scheduler, shared dashboard/automatic/v1 consume paths, account summaries, account/dashboard query caches and regression suites. Existing settings and API shapes remain compatible; no deployment or production mutation is part of this change.
