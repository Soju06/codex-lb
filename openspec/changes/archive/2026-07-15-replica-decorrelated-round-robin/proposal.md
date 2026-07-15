# Decorrelate round-robin tie-breaking across replicas

## Why

The `round_robin` account selector orders candidates by planner cost, then
least-recently-selected time, then a stable `account_id` tie-break. In a
multi-replica deployment every replica shares the same account set and evaluates
the identical sort key, so on an *exact* tie of the primary keys (e.g. a cold
start where every account is never-selected, or several accounts that reset to
`last_selected_at = 0.0`) every replica breaks the tie toward the same
lexicographically-first `account_id`. The result is herding / thundering-herd:
all replicas pick one account first instead of spreading equal load across
equally-good accounts. This is the deferred multi-replica split from the
propagate-balancer-health-signals design.

## What Changes

- Add a per-replica salt (defaulting to the HTTP responses-session bridge
  instance id, else the host identity) mixed into the **final** round-robin
  tie-break via a keyed hash.
- Primary ordering (planner cost, then least-recently-selected) is unchanged;
  only genuine ties are decorrelated so peers spread across equally-good
  accounts.
- The salt is process-stable (not random per call), so single-replica selection
  stays deterministic and unchanged in aggregate.
- No schema/migration change: the salt is in-memory routing state only.
