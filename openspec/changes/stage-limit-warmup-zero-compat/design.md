## Context

The existing dashboard settings table and both settings schemas reject zero,
while the follow-up activation release needs zero to represent the default
reset-confirmed threshold. The compatibility release therefore has to widen
the wire contract before the activation migration changes the database source
of truth.

## Goals / Non-Goals

**Goals:**

- Make zero valid on both API directions before it can appear in a response.
- Preserve the current public/default behavior (`99`) during the staged rollout.
- Put the future column in the Alembic graph with a reversible, nullable shape.
- Prove the contract with backend, frontend, and migration tests.

**Non-Goals:**

- Do not change warm-up candidate selection or enable a zero threshold.
- Do not expose zero in the dashboard form.
- Do not synchronize the legacy and staged columns with triggers or dual writes.
- Do not backfill existing rows or change the legacy column's default.

## Decisions

1. **Widen schemas before activation.** Backend and frontend bounds change from
   `> 0` to `>= 0`; this prevents old replicas from rejecting a future zero
   response or update. The UI retains its current `min=1` and validation.
2. **Normalize zero at the repository boundary.** A direct update of zero is
   accepted as a compatibility probe, then stored as legacy `99.0` and returned
   as `99.0`. This avoids publishing a sentinel before the activation migration
   owns the column and keeps the old runtime behavior intact.
3. **Use an inactive nullable column.** The ORM maps the new SQL column under an
   internal compatibility attribute with no default. The existing runtime
   attribute continues to map to the legacy column until the activation PR
   switches the source of truth.
4. **Use a forward-only migration from the current main head.** The migration
   adds only the nullable column. Its downgrade drops only that column, so it is
   safe to roll back this stage without rewriting operator data.

## Risks / Trade-offs

- **[Risk]** A client may send zero before activation and expect it to persist.
  **Mitigation:** the staged contract explicitly normalizes it to `99.0`; the
  activation PR owns the behavior change.
- **[Risk]** A nullable compatibility column is not represented by old ORM
  code. **Mitigation:** it is additive and nullable, and the follow-up PR
  carries the ORM/runtime cutover.
- **[Risk]** The new revision could create an Alembic head. **Mitigation:** set
  its `down_revision` to the current main head and run migration graph checks.

