# Company source governance

## Why
Company model sources need visible operational state and local usage controls before production activation.

## What Changes
- Derive health, recent failures, latency and cooldown from persisted request outcomes.
- Reject requests during cooldown and when an optional rolling 24-hour observed-token budget is exhausted.
- Show operational state and allow editing the local budget in source settings.

## Impact
Company Responses and Chat routes, model source dashboard API, one nullable database column. Subscription routing is unchanged.
