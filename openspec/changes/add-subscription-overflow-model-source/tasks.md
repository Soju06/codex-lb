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

### 3.x WP-C1 source-dispatch owner (direct source routing hardening; no overflow wiring)

- [x] 3.2 `app/modules/proxy/source_admission.py`: per-source bulkhead enforcing `ModelSource.max_concurrency` (`NULL` unlimited), `SourceAdmission` claims released exactly once by whichever latch holds them (`release_if_unowned` route latch, `release(trial_result)` owner latch), `TrialClaim` hook for the WP-C2 breaker; direct routing claims before the API-key reservation and answers `503 model_source_busy` + `Retry-After: 1` with nothing owned.
- [x] 3.3 `app/modules/proxy/source_dispatch.py`: `SourceDispatch` single latch (`close_source -> settle_or_release -> release_claims + record_result -> write_row`, every step independently latched, failure-isolated, cancellation deferred), `abandon()` cancellation classes (`client_disconnected_during_open`, `source_stall_abandoned`, `client_disconnected_before_body`, `dispatch_interrupted`), `finalize_transport()`, `SourceStreamingResponse`, `open_with_disconnect_watch` (owned open task, 250 ms disconnect poll, 10 s stall evidence, child always cancelled and awaited), `settlement_stream` outermost body layer, settle-at-estimate policy, verified pin-write hook + synthesized `response.created`/`response.failed` pair (primitive only; never armed by direct routing).
- [x] 3.4 `app/modules/proxy/api.py`: `_source_responses_response` rewired onto the owner for limited and unlimited keys (live streaming; the Responses limited-key buffered branch is removed, chat completions keep theirs), telemetry stripped through `strip_source_telemetry`, raced open, non-stream disconnect check, source `Retry-After` merged into pre-open error headers, `context` threaded from both routes for the scheduler/clock/cleanup seams; api.py timing allowances unchanged (19/8).
- [x] 3.5 `app/core/metrics/prometheus.py`: `codex_lb_model_source_dispatch_total{kind,status}`, `_dispatch_abandoned_total{stage}`, `_timeout_total{phase}`, `_bulkhead_rejections_total{source_id}`, `_bulkhead_in_flight{source_id}`, `_usage_estimated_total{source_id,cause}`, `codex_lb_model_source_live_pins{kind}` (registered; sampler lands with WP-C2) in both the real and the stub branch.
- [x] 3.6 Tests: `tests/unit/test_source_admission.py`, `tests/unit/test_source_dispatch.py` (virtual-time watch, latch, settle figures incl. a hypothesis property, synthesized pair, transport finalizer), `tests/integration/test_model_source_dispatch.py` (real stub relay: live limited-key streaming, estimate on missing usage and post-item cancel, release before, non-stream `usage_unavailable`, abandonment cases a-e, `max_concurrency=1` busy, `Retry-After` passthrough, row attribution, telemetry stripped at the stub, static zero-hot-path ratchet); `api-keys` and `proxy-runtime-observability` deltas.
