## 1. OpenSpec artifacts

- [x] 1.1 Create proposal, specs, and design artifacts for warmup endpoint and accounting rules.

## 2. Data model and migrations

- [x] 2.1 Add `dashboard_settings.warmup_model` to DB model and settings data flow with default `gpt-5.4-mini`.
- [x] 2.2 Add `request_logs.request_kind` to DB model, request-log API schema, and mapper path.
- [x] 2.3 Add Alembic migration(s) for both new columns with safe backfill behavior.

## 3. Warmup API and service flow

- [x] 3.1 Add `POST /v1/warmup` route and request/response schemas.
- [x] 3.2 Implement warmup mode logic (`default`, `all-or-none`, `force-update`) with strict primary-window eligibility checks.
- [x] 3.3 Implement scoped/unscoped target-pool resolution from API key behavior.
- [x] 3.4 Implement upstream warmup submission and per-account result summary generation.
- [x] 3.5 Run warmup submissions in parallel with a fixed max concurrency of 5 accounts.

## 4. Logging and accounting semantics

- [x] 4.1 Persist warmup executions with `request_kind="warmup"` while preserving normal requests as `normal`.
- [x] 4.2 Exclude warmup rows from dashboard aggregate request/error/token/cost and top-error queries.
- [x] 4.3 Exclude warmup rows from API-key usage summaries and trend aggregations.
- [x] 4.4 Exclude warmup rows from account request usage summary aggregates.

## 5. Frontend settings and dashboard visibility

- [x] 5.1 Extend settings API/frontend schemas and payload builder for `warmupModel`.
- [x] 5.2 Add warmup model control to settings UI.
- [x] 5.3 Show warmup marker in recent request log rows/details.

## 6. Verification

- [x] 6.1 Add/adjust backend tests for warmup modes, strict all-or-none rejection, and request-log tagging.
- [x] 6.2 Add/adjust aggregate tests proving warmup exclusion from dashboard and API-key accounting paths.
- [x] 6.3 Add/adjust frontend tests for settings schema/control and warmup request-log rendering.
- [x] 6.4 Add/adjust backend test coverage for bounded warmup parallelism (max 5 in-flight submissions).
