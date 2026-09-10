## Context

See [proposal.md](proposal.md) for the reproduced two-head upgrade failure and [context.md](context.md) for the affected parent histories.

## Goals and non-goals

Join the two existing migration histories without rewriting an applied revision. The new merge changes only Alembic version stamps. Receipt lifecycle policies remain outside this repair.

## Decisions

Use a new explicit merge revision with both current heads as parents. Alembic applies the missing parent before reaching the merge; reparenting either old revision would change the meaning of existing version stamps.

Use the public upgrade/check CLI for regression proof from both populated parents. For merge-only downgrade, name either immediate parent because relative `-1` is ambiguous. Require both parent stamps and exact schema/data equality afterward, then re-upgrade to one head.

## Risks and trade-offs

A schema-neutral merge does not make a one-parent application build compatible with both schemas. Downgrade only the merge for this proof; removing receipt columns remains subject to the existing live-receipt guard. A later independent migration branch can require another explicit merge, so verify the graph against the actual target before delivery.

## Migration plan

Upgrade either existing parent through the documented `codex-lb-db upgrade head` command, then run `codex-lb-db check`. The merge itself has no data backfill. Keep receipt and spool values intact across upgrades and merge-only downgrades. Deployment and receipt-policy acceptance remain separate from this graph correction.
