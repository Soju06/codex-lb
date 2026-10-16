## Context

See proposal.md. Local head `734d28c7f` and published head `f7cd85f75` contain independently reviewed implementations. The published branch adds duration-matched overrides and reserve presentation; the local branch contains newer upstream work and additional authorization-boundary fixes.

## Goals / Non-Goals

Preserve both histories and their observable fixes in one PR. Retain existing ownership, cancellation, injected timing, and default-disabled behavior. This integration does not split or replace the feature.

## Decisions

- Merge the published branch and reconcile contracts at each conflicting boundary. Preserve its override fields and evaluator, then retain local dispatch/preparation checks, sticky generation fences, empty-poll placeholders, and canonical errors.
- Reuse typed authorization and usage-snapshot contracts already present on both branches, rather than maintain competing policy readers.
- Retain the original scalar migration lineage and add an explicit merge joining the published override head with the local upstream merge head. Verify actual schemas from both graph variants, including databases that already applied upstream before the override revision.
- Retain all distinct regression scenarios; consolidate equivalent assertions only when public-path coverage remains.
- Push normally to the existing PR branch only after the combined code and graph pass their affected checks.

## Risks / Trade-offs

- Published and local scalar migrations have different parent declarations for the same revision identifier. Historical-schema upgrade tests must prove that convergence neither assumes missing upstream schema nor fails when upstream schema already exists.
- Authorization fixes must account for override-only policies; checking only the scalar threshold can silently bypass an enabled policy.
- Frontend mock and summary contracts include derived effective limits and reserve displays; preserving fields without their duration semantics would break the editor and dashboard.
