# Absolute burn-first precedence

## Purpose and scope

`burn_first` is an operator instruction to consume expendable account capacity
before every other eligible account. It is not a hint to an individual balancing
strategy. The selector first applies hard request eligibility and then treats the
highest-priority nonempty manual-policy pool as the complete candidate set for
all remaining routing decisions.

## Decision and alternatives

Policy precedence sits above strategy ranking, health preference, budget
preference, `single_account`, sticky mappings, preferred-account hints, owner
lookups, and retry selection. Keeping affinity or `single_account` above the
policy was rejected because it allows normal accounts to keep receiving traffic
indefinitely while eligible expendable capacity remains.

## Constraints

The policy does not bypass authorization, API-key account scope, requested-model
plan support, additional-quota eligibility, explicit retry exclusions, cooldown,
rate-limit state, or local concurrency admission. Additional-quota policy
overrides are evaluated as the effective policy for that request.

Account-local file and previous-response identifiers are not portable between
upstream accounts. Absolute precedence still selects an eligible `burn_first`
account before an owner-derived preference; if upstream rejects the referenced
identifier on that account, normal error and retry handling applies and MUST NOT
silently route back to a lower-priority policy pool while an eligible
`burn_first` account remains.

## Failure modes and operations

An unavailable or ineligible burn-first account is removed by the existing hard
filters, allowing the next policy pool to serve traffic. Operators should monitor
upstream invalid-reference errors after enabling burn-first on workloads that
reuse account-local files or previous responses. Removing or changing the policy
restores ordinary affinity behavior on the next selection.

## Examples

If `account-a` is the configured `single_account` and `account-b` is eligible and
marked `burn_first`, new traffic selects `account-b`. If a sticky session points
to `account-a`, the mapping is rebound to `account-b`. If `account-b` is paused,
outside the API-key account scope, or excluded after a failed attempt, the
selector falls back to the highest-priority remaining eligible pool.
