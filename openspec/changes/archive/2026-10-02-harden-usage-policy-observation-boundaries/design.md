## Context

See proposal.md for motivation. Warmup services share an AsyncSession with claim and settlement repositories; authorizing projected account fields must not dirty the persistent identity map. Each quota-planner warmup already receives or loads a dashboard snapshot before claiming admission.

## Goals / Non-Goals

Keep read authorization independent of durable account writes and cover post-commit cancellation. Preserve the default, 5-hour and weekly policy semantics, existing reservation settlement, and migration history.

## Decisions

- Copy warmup accounts with the existing transient `clone_row` helper before merging projected policy/status. This retains the Account contract without a new abstraction or unsafe identity-map writes.
- Bind resilience from the dashboard snapshot already used for the warmup budget. Removing the second asynchronous settings read keeps final authorization adjacent to dispatch and avoids inconsistent settings within a single warmup.
- Start the post-commit invalidation `finally` before re-reading policy. Until that read succeeds, invalidate conservatively; this includes cancellation without turning cancellation into an authorization outcome.
- Remove unused exception and cached-input-sharing parameters. Cached selection inputs remain privately cloned for each caller.

## Risks / Trade-offs

Policy updates after the final dispatch observation do not cancel admitted work. Failed/cancelled post-commit policy reads may cause one conservative cache invalidation. No database locks are held across upstream traffic and no new query or setting is introduced.
