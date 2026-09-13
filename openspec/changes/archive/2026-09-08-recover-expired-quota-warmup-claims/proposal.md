# Proposal: recover expired quota warmup claims

## Why

PR #1646 introduced expiring quota-warmup claims, but current-head review still
found four contract gaps:

- the claim TTL can expire long before the warmup probe's HTTP stream budget;
- the scheduler only revisits expired claims when a fresh planner action points
  back at the same decision;
- a reclaimed decision cannot tell whether a prior attempt already committed a
  successful warmup request log; and
- the lease-expiry Alembic revision lacked its own migration-path coverage.

These are all part of the same quota-phase-planner claim-recovery contract.

## What Changes

- Keep warmup execution claims alive for at least the full configured Responses
  stream budget.
- Give each warmup decision a stable warmup request id so a reclaimed claim can
  reconcile from an already-committed request log instead of sending a duplicate
  probe.
- Add a scheduler reconciliation sweep for expired `executing` warmup claims
  that is independent of the current planner output.
- Add migration coverage for the `lease_expires_at` Alembic revision.

## Impact

- Prevents duplicate synthetic warmup traffic after a process dies between the
  durable request-log commit and the final decision-status update.
- Lets manual and no-longer-planned stale claims self-heal on the next scheduler
  tick instead of waiting for a matching future planner action.
- Documents the new recovery contract in OpenSpec.
