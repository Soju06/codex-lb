# Quota continuity recovery

## Scope

Native deterministic failover and transport retry budgets remain authoritative.
This change repairs quota-specific continuity and soft affinity. It does not
extend retry counts, add sleeps, or suppress ordinary native retries.

## Ownership

A pre-visible rejection must not establish a request-local hard owner.
Verified full-history replay can discard obsolete continuation anchors, but
file, durable operation, ambiguous affinity, single-account policy, accepted
work, and unverifiable history remain fail-closed. Existing response owners
and old session histories are never rebound to the replacement account.
Soft-affinity cleanup uses compare-and-set on the affected request mapping.

## Settings

The default-on `quota_failover_enabled` field controls only this extension.
Advanced → Resilience displays Quota continuity recovery below Deterministic
failover using the existing switch/card design. No new environment variable.

## Verification and deployment

Test native failover with recovery on/off, native retry ceilings, safe and unsafe
continuations, leases/settlement, concurrent soft-pin reassignment, frontend save
behavior, and migrations. Deploy only Codex LB after backup and migration
rehearsal, preserving the two warm-up patches. Update PR #2207 after production
health and rendered UI verification. No new independent review.
