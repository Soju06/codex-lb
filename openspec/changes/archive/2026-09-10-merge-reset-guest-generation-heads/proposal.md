# Compose reset pooling with guest-session generation

## Why

Main adds guest-session generation above the spool-retention revision. Its composition with the published reset/spool merge leaves two Alembic heads, reproduced by the public upgrade-head CLI.

## What Changes

Append an explicit no-op merge revision. Preserve both published migration histories, stored guest generation and reset bindings. Keep the historical merge-only tests pinned to their named revisions, then verify latest-head drift. Preserve exact known defaults in the conflicted historical backfill test.

## Impact

No guest, admin or Desktop reset permission policy changes. Main's complete permission, guest revocation and protected-search contracts remain intact.
