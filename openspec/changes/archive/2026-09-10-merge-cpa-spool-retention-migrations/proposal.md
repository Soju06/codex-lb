# Reconcile CPA discovery with the current migration graph

## Why

Upstream main 6d11e560c adds dashboard spool retention from the same migration parent as CPA catalog discovery. Combining the branches creates two Alembic heads. The public `codex-lb-db upgrade head` command fails on the combined checkout.

## What changes

Add an explicit no-op Alembic merge revision after both migrations. Preserve both existing revision identities and parent relationships so databases that already applied either branch can acquire the other branch's schema. Update CPA migration tests to follow the unified head and prove populated upgrades from both branches.

## Impact

This reconciles PR #2305 and issue #2290 with accepted upstream schema changes. There is no new product setting or provider behavior. Verification uses the migration CLI/startup contract and dedicated disposable databases. Live databases and deployment remain outside scope.
