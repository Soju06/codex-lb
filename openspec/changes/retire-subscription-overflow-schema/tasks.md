- [x] Add `20260914_000000_drop_subscription_overflow_schema` on
  `20260913_000000_add_oidc_provider_flow`: drop `model_source_pins` (taking
  both indexes with it) and the two `dashboard_settings` columns, each step
  guarded on the object being present.
- [x] Downgrade restores both revisions' work: the two nullable columns, and the
  table with its primary key and no foreign key on `source_id`. Reflect each
  index independently of table creation so an interrupted downgrade or a
  partial restore still ends with both indexes.
- [x] `app/db/models.py`: delete `ModelSourcePin` and the two
  `subscription_overflow_*` columns from the settings model.
- [x] Re-anchor the merge revision's tests at
  `20260908_020000_merge_overflow_transport_heads`: seed and assert row
  preservation and the direct parent/merge round-trip there, and assert the
  retirement once at head. Keep the transport branch's rows asserted unchanged
  so the claim stays a fact about the merge, not about the withdrawal.
- [x] Invert the three head-state assertions in `test_migrations.py` (both
  settings columns and the pins table are gone at head) while each revision's
  own build assertions stay at its own revision. Delete the pin-index query-plan
  test: its subject was the plan of a dashboard query that no longer exists,
  against a table that no longer reaches head.
- [x] Regression test for the index-repair path: at the retirement revision,
  plant `model_source_pins` with neither index, downgrade to the parent, and
  assert both indexes and both settings columns are back. Verified it fails on
  the pre-fix revision (0 indexes) and passes on the fixed one.
- [x] State the drain requirement for the *upgrade* direction, not only for
  rollback: every release below this one maps both settings columns and loads
  the settings row whole, and the chart's migration Job is a `pre-upgrade` hook,
  so pre-withdrawal replicas must be stopped before the drop commits. Recorded
  in the revision docstring, the proposal, the spec delta and
  `docs/deployment/kubernetes.md` (next to the legacy-credential drop's own
  section). No pre-DDL warning is added: `check_legacy_credential_drop()` is
  written around the credential columns, and generalizing it is separate work.
- [x] Spec delta: `database-migrations` — MODIFIED "Overflow and transport
  migration heads converge without rewriting history" (anchored at the merge
  revision, with a scenario for continuing to head), ADDED "Withdrawn overflow
  storage is retired without stranding an intermediate install".
- [x] Verification: `scripts/check_migration_topology.py` (single head, clean
  against `origin/main`), `check_schema_drift` at head, ruff, ruff format, `ty`,
  the five architecture guards, `openspec validate --specs` and
  `openspec validate retire-subscription-overflow-schema --strict`,
  `tests/integration/test_migrations.py` +
  `test_migration_merge_overflow_transport.py` on SQLite and on a throwaway
  PostgreSQL 17 (every PostgreSQL-only test actually executed, 0 skipped).
