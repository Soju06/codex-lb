# Merge CPA and dashboard identity histories

## Why

PR #2305 at14e6286 combined with main8e5760726 fails populated public upgrade-head with MultipleHeads. Main appends roles, users, compatibility credential reprojection and audit attribution after guest generation; the published CPA merge remains a separate leaf.

## What changes

Append a no-op merge after both complete histories. Preserve all published revision blobs. Verify populated source data and role/user identities, grants, credentials and guest generations; retain populated audit history and the existing identity migrations' documented legacy credential conversion. Prove each-parent and both-parent upgrades, merge-only downgrade/re-upgrade and schema drift. Verify CPA management and revocation under current roles, users and CSRF rules.

## Impact

Keep issue #2290 independently usable on pinned main. No sibling dependency, new permission policy, provider change or live deployment is introduced.
