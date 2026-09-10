# Verification

Target main is `6d11e560c9324a5f2ad7a0c6260780f1d2df2ea9`; the preceding PR candidate is `b8a33ae476e40fe878ec1179dd9fcc30606beb6a`. Their textual merge succeeds, but the public upgrade-head CLI and both populated-parent tests reproduce MultipleHeads before the repair. This is a migration-graph composition defect, not an operator setup error.

The no-op merge joins the existing reset merge and spool-retention revisions without rewriting either parent. The old merge-only test targets its named historical merge, preserving its original parent-stamp and binding assertions; it still upgrades to the latest head and checks full schema drift afterward. New tests cover both new direct parents, preserving exact reset binding, enabled reset policy, explicit retention value, nullable inheritance, merge-only downgrade stamps and re-upgrade schema drift.

The affected settings/reset/migration run passed 124 cases with 6 PostgreSQL-only cases skipped locally. Public CLI upgrade/check reports one head, migration policy ok and no schema drift. Ruff, full type checking, architecture checks and 67 main specifications pass. Both database variables point to the same disposable database before imports and tests. Hosted PostgreSQL proof remains required for the new committed candidate; b8a33ae47's passing hosted run covers the previous main composition only.

No live database, Docker, routing or reset-credit consumption occurred. Main's removed recovery selector remains removed; no product policy was added.

Independent GPT-6 Astra Medium read-only review found no Standards or Input findings and no weakened preservation oracle. It did not rerun tests or substitute local review for hosted PostgreSQL proof.
