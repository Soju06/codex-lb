# Reconcile CPA and guest-session migration histories

## Why

Combining PR #2305 at fb81474d with upstream d6a7ca66 creates two Alembic heads. A populated database at the published CPA/spool merge fails the public upgrade-head command on the combined checkout. Guest-session generation descends from spool retention while CPA's existing merge also includes that revision.

## What changes

Append a no-op merge after guest-session generation and the published CPA/spool merge. Preserve every existing revision and parent relationship. Prove upgrades from either head and both-head states, populated data preservation, merge-only downgrade/re-upgrade, and guest/CPA permission compatibility.

## Impact

This keeps issue #2290 independently usable on pinned main. No provider behavior, new setting, permission policy, or deployment changes are introduced. Existing CPA scheduling/oracle fixes and provider verification limits remain intact.
