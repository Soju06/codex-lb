## Latest upstream integration

The beta.7 rebase incorporates main `efe0f581`, including dashboard-managed
stream/bridge budgets and Codex session prewarming, HTTP continuation
promotion, native HTTP terminal completion, pinned automation claim budgets,
topology-bound settings tier classification, and constantized core/bridge tunables.
Main's 34 removed tunables stay removed; this branch retains its ten opt-in
transcript/recovery controls, for 106 environment settings. The settings budget,
tier registry, and generated reference agree on that inventory.
The release branch differs from main only in release version metadata; the
feature PR retains main's version metadata because beta publication belongs to
the canonical release branch.

The recovery/report join and automation claim-budget revision share historical
ancestors but are separate heads. A new metadata-only revision joins them without
rewriting either history. For example, a database already at the recovery/report
join receives the claim-budget column when upgraded to the new head; a database
at the automation revision receives the recovery branch. Existing rows survive
both upgrade paths. Downgrading only the join restores parent version stamps and
does not remove schema or data.

The subsequent prewarm migration branches from the automation revision. A new
metadata-only join converges it with the existing recovery/automation merge,
preserving both historical parents and the nullable dashboard override semantics.
The subsequent stream/bridge budget revision extends the prewarm branch; another
metadata-only join preserves the earlier recovery/prewarm join and adds the two
nullable request-budget override columns through the upstream migration.

Tests assert exact parent relationships, one head, full schema equivalence across
the metadata-only join, row preservation, and a drift-free final schema. The
rebind-claim round-trip setup also applies the independent claim-budget branch
before comparing against the full current ORM schema. The separate populated
overflow/transport regression retains its exact latest-head assertion alongside
the historical-parent and data-preservation assertions.
