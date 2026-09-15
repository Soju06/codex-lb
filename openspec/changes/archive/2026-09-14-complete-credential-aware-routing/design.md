## Decisions

Reuse the existing credential availability predicate rather than duplicating reason strings. Keep access use distinct from proactive refresh eligibility. Derive persistence-conflict reasons from each fresh row under the existing CAS. Separate per-account repair epochs from global snapshot epochs so unrelated repairs cannot veto rejection, while newer same-account observations still win.

## Constraints

Preserve account ownership, operator exclusions, and settlement order. Avoid new settings and schema. Regressions must exercise consumer behavior and deterministic repair/rejection interleavings.
