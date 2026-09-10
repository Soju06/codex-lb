# Merge CPA and dashboard invitation histories

## Why

Published PR #2305 at9b4d98 combined with main561311de has two Alembic heads. The populated public upgrade command fails because the invitation migration follows audit attribution, beside the existing CPA identity merge.

## What changes

Append a no-op merge joining both complete histories without changing any published revision. Verify each-parent and both-parent upgrades, populated invitation and identity/source preservation, merge-only downgrades, re-upgrade and schema drift. Retain historical merge tests at their own named revisions. Check current user/invitation permission behavior alongside CPA management.

## Impact

No new permission policy, provider behavior, sibling dependency or live action. Current source/identity/audit contracts remain unchanged.
