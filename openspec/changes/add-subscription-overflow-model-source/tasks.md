## 1. Schema (WP-A)

- [x] 1.1 Add `subscription_overflow_source_id` and `subscription_overflow_drain_until` to `DashboardSettings` and the `ModelSourcePin` model with its `purge_at` index.
- [x] 1.2 Add Alembic revision `20260908_000000_add_subscription_overflow` on the current head with guarded, idempotent upgrade and full downgrade.
- [x] 1.3 Cover upgrade/downgrade, idempotent re-run against a partially applied schema, and PostgreSQL invalid-index repair in `tests/integration/test_migrations.py`.
- [x] 1.4 Run migration policy and drift checks on SQLite and PostgreSQL and strict OpenSpec validation.

## 2. Designation settings, preflight, drain deadline, dashboard (WP-B)

- [ ] 2.1 Extend this change with the `model-source-routing` designation requirements and implement the inert settings API tri-state, preflight endpoint, delete-source clearing, dashboard control, i18n, and docs.

## 3. Pins, routing, observability, rollout (WP-C1, WP-C2, WP-D, WP-G)

- [ ] 3.1 Extend this change with the pin repository, overflow routing, observability, and rollout requirements.
