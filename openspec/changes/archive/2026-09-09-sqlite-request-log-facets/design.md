## Context

The recursive `MIN(facet)` queries combine ordered traversal with `deleted_at IS NULL` and the supported-status predicate. Without statistics, SQLite chooses the soft-delete index for the seed and every successor, then finds the minimum by scanning the whole live cohort. See [context.md](context.md) for the runtime evidence.

## Goals / Non-Goals

Preserve the existing recursive traversal and response contract while making SQLite use its facet indexes without operational database changes. PostgreSQL, filtered requests, schema, and dashboard rendering are outside this change.

## Decisions

For SQLite, enumerate candidate values with `MIN(facet)` and a strict successor range. Apply visibility in a correlated `EXISTS` with `facet = candidate`. For the second column of a pair, retain the leading-column equality in the traversal so the composite index remains usable. Keep the existing NULL-pair probe.

The current PostgreSQL traversal retains all predicates. SQLite's candidate enumeration can visit values that have no visible rows; the final eligibility probe excludes them. PR #2246's live-row partial indexes remain useful to these eligibility probes.

Keep this within the existing two facet-query methods. They own the traversal and pair semantics; extracting a separate module for this bounded change would add a second repository interface without separating a responsibility.

Alternatives checked:

- SQLite `DISTINCT` still scans the live cohort and was slower than the proposed query on the synthetic corpus.
- Full `ANALYZE` improved plans, but bounded `PRAGMA optimize` did not reliably fix the reproduced plan. Requiring an operational statistics write would also leave the default query behavior dependent on maintenance.
- Adding indexes alone did not correct the plan without statistics in the synthetic comparison with PR #2246.
- Forcing index names or disqualifying predicates would couple the query to a specific index choice and could defeat future partial indexes.

## Risks / Trade-offs

Candidate count includes historical values that are no longer visible. Eligibility can still inspect a dead-only cohort to establish absence. This change removes repeated full live-cohort scans; it does not promise constant work for arbitrarily large dead-only value sets.

The pair-prefix predicate must be retained during traversal, and the original predicates must remain inside eligibility. Tests cover NULL and empty values, unsupported statuses, deleted-only values, and visible/deleted overlap.

## Migration Plan

Deploy through the normal reviewed release process. There is no data migration or configuration change. Rolling back the code restores the prior query behavior. The local running service is not changed by this task.
