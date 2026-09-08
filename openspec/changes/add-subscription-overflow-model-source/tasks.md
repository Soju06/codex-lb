## 1. Schema (WP-A)

- [x] 1.1 Add `subscription_overflow_source_id` and `subscription_overflow_drain_until` to `DashboardSettings` and the `ModelSourcePin` model with its `purge_at` index.
- [x] 1.2 Add Alembic revision `20260908_000000_add_subscription_overflow` on the current head with guarded, idempotent upgrade and full downgrade.
- [x] 1.3 Cover upgrade/downgrade, idempotent re-run against a partially applied schema, and PostgreSQL invalid-index repair in `tests/integration/test_migrations.py`.
- [x] 1.4 Run migration policy and drift checks on SQLite and PostgreSQL and strict OpenSpec validation.

## 2. Designation settings, preflight, drain deadline, dashboard (WP-B)

- [x] 2.1 Extend this change with the `model-source-routing` designation and preflight requirements.
- [x] 2.2 Settings API: tri-state `subscription_overflow_source_id`, eligibility validation (`400 subscription_overflow_source_invalid`), drain deadline arming/clearing in the same row update, audit `changed_fields`, cross-replica cache invalidation.
- [x] 2.3 `GET /api/settings/subscription-overflow/preflight` (write access; 404 for unknown sources; warnings never block).
- [x] 2.4 Deleting the designated model source clears the designation and arms the drain deadline in the delete's transaction, then invalidates the settings cache.
- [x] 2.5 Dashboard Routing card: designation select (Off sentinel), drain notice showing and gated on the derived pin expiry, inline preflight, help text, i18n en/ko/zh-CN, vitest coverage.
- [x] 2.6 `docs/routing.md` operator explainer linking back to `model-source-routing`.
- [x] 2.7 Inertness proof: request-path/core ratchet unit test plus the end-to-end test that an exhausted pool still answers `429 usage_limit_reached` with a source designated and never contacts it.

## 3. Pins, routing, observability, rollout (WP-C1, WP-C2, WP-D, WP-G)

- [ ] 3.1 Extend this change with the pin repository, overflow routing, observability, and rollout requirements.
