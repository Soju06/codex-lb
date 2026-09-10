## Context

The incoming guest-generation revision descends from spool retention, alongside the already published receipt/spool merge. See context.md for exact revisions.

## Decisions

Append an explicit no-op join with both current heads as parents. Never edit published migration identifiers, edges or bodies. Preserve both sides of the historical migration test conflict: compare retained fields and assert the incoming guest generation defaults to zero.

Exercise the public upgrade/check CLI from both populated parents. A named-parent downgrade of only the new join must restore both parent stamps with exact schema/data equality. Preserve the earlier join's round-trip regression as historical coverage. Run guest session and model compatibility tests on the combined tree without changing auth behavior.

## Risks and trade-offs

The new join proves database composition, not mixed-version activation or receipt lifecycle acceptance. A future target advance may add another branch; assess that exact change before reusing composition proof.
