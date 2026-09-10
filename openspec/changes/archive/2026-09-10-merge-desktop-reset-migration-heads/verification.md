# Migration verification

The original graph failed `run_upgrade(url, "head")` with multiple heads. The populated reset-branch regression failed with the same error before the merge revision was added.

The merge preserves both original histories. CLI upgrade and check report revision `20260910_010000_merge_desktop_reset_pool_heads`, migration policy OK and no schema drift.

Focused desktop, overflow merge, general migration and migration-tool suites passed with 110 passed and 12 skipped. PostgreSQL cases were skipped because only dedicated disposable SQLite databases were available. A final focused run confirmed both populated branch upgrades and direct merge downgrades preserve settings and exact redemption owner/credit bindings, with 2 passed and 2 PostgreSQL cases skipped. Ruff lint and format checks passed. The change passed strict OpenSpec validation.

Both database environment variables pointed to the same dedicated disposable SQLite database for each invocation. No live database or container was used. Hosted PostgreSQL verification remains with the PR owner.
