# Verification

Target main: `d6a7ca662860e2b427e462f434319273bcd240bd`. Previous candidate: `55b2fc0740de71d403069007b0844f2c4e82e8bd`. Its historical hosted proof is retained separately and does not verify this new composition.

The only textual conflict was the historical overflow migration backfill assertion. Resolution keeps exact zero defaults for reset pooling and guest generation, with nullable defaults unchanged. After composition, the public upgrade-head CLI and both populated-parent migration tests reproduced MultipleHeads before the repair. This is a migration-graph defect rather than an operator configuration error.

The new no-op merge joins the published reset/spool merge and guest-generation revision without reparenting either. Populated tests preserve exact reset owner/credit bindings, reset policy, admin and guest credentials, guest-access state, explicit retention settings and nonzero generation through upgrade and merge-only downgrade. A reset-parent database receives generation zero. Historical merge tests retain their original named-merge assertions and still finish with latest-head schema drift checks.

The first run passed 77 focused reset, guest, permission, protected-search/count and migration controls, with 8 PostgreSQL-only skips. A second auth/migration control run passed 71 tests, with 8 PostgreSQL-only skips. These runs overlap and must not be summed. The public guest-session test also confirms that guests cannot enable reset pooling, while an authorized reset-setting change keeps existing guest sessions valid. Main's auth dependencies, dashboard auth/access, request-log privacy, account mapper and dashboard paths remain byte-identical to the target.

Ruff, architecture checks, full type checking and all 67 main specifications pass. Public CLI upgrade/check reports one head, valid migration policy and no schema drift. Both database variables target the same dedicated disposable database before imports or tests. New-head hosted PostgreSQL and review evidence remain required after publication; no old hosted result is carried forward as current proof.

No live Docker, database, routing, real reset consumption or product policy change occurred.

Independent GPT-6 Astra Medium static review found no Standards or Input findings, no published-migration rewrite, no policy expansion and no weakened preservation oracle. It did not rerun tests or substitute for hosted PostgreSQL verification.
