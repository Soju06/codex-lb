## Context

The composed tree at `2a7f7c43d` reproduces the public CLI multiple-head failure. Both parents descend from the request-log cost-index revision.

## Goals / Non-Goals

Join existing history without changing recovery, generation, scope or spool-retention semantics. Deployment is outside this change.

## Decisions

Add a no-op two-parent merge. Reparenting would rewrite existing migration history and is excluded. Test the public CLI and inspect populated database state on SQLite and PostgreSQL. A merge-only downgrade retains both parent stamps; it does not remove one branch.

## Risks / Trade-offs

A later independent migration can create another head. Pin the current target and recheck the graph before handoff. Missing-parent migrations retain their existing defaults and downgrade behavior.

## Migration Plan

Upgrade normally to head after maintainer acceptance. For example, a database with saved spool retention applies rejection-generation columns with generation zero while preserving retention. Downgrading only the merge changes stamps and keeps both parent schemas.
