## Why

Upstream already provides deterministic failover, soft drain, routing weights,
overload isolation, and bounded transport retries. Those mechanisms do not
recover every quota-rejected continuation or retire its exhausted soft affinity.

## What Changes

- Preserve native failure classification, retry limits, delays, and selection.
- Retire only the affected soft mapping on confirmed usage exhaustion using
  compare-and-set; preserve hard/durable owners and concurrent reassignment.
- Permit verified account-neutral full-history continuations to detach before
  acceptance, omitting the old response anchor and account-scoped headers.
- Keep incomplete history, uploaded files, durable operations, accepted work,
  and single-account policy fail-closed.
- Expose the default-on recovery switch in Settings → Advanced → Resilience,
  directly below Deterministic failover. It is not a general retry kill switch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: verified quota continuity recovery within native retry bounds.
- `sticky-session-operations`: targeted exhausted soft-affinity retirement.
- `frontend-architecture`: an independent Resilience recovery switch.
- `database-migrations`: persist the default-on setting.

## Impact

Proxy continuity preparation, sticky cleanup, settings API/UI, and additive
migrations. No extra retry loop, retry delay, environment variable, or dependency.
