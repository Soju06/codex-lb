# Merge the overflow and transport migration heads

## Why

Main contains two already-merged revisions descending from the quota-warmup
revision: subscription-overflow from #2165 and transport-sentinel replacement
from #2192. A normal upgrade to `head` cannot choose between them. Reparenting
unmerged feature migrations does not repair that shared graph.

## What changes

Add one forward merge revision joining both heads, with no schema or data
operations. Keep both merged migrations unchanged. Verify populated upgrades
from either parent and from both parents, plus downgrade of the merge itself
to either parent and re-upgrade.

## Scope and non-goals

Only Alembic graph repair, its regression tests and OpenSpec records are in
scope. This adds no settings, new setup step, routing or receipt policy.
PR #1954 remains unchanged and dependent on upstream acceptance of this repair.
Its lease-policy and late-receipt recovery limitations are not resolved here.
