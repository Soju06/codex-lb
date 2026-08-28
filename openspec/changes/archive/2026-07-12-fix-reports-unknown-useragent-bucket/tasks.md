# Tasks: fix-reports-unknown-useragent-bucket

## 1. Spec

- [x] 1.1 Add a `frontend-architecture` delta for preserving null user-agent traffic as `Unknown` in reports filtering and distribution rendering.

## 2. Backend Reports Behavior

- [x] 2.1 Aggregate `request_logs.useragent_group = null` into a `byUseragent` bucket labeled `Missing User-Agent`.
- [x] 2.2 Preserve real `useragent_group=Unknown` traffic as its own bucket while filtering `Missing User-Agent` to null-backed report rows.

## 3. Frontend Reports Rendering

- [x] 3.1 Render the `Missing User-Agent` `Distribution by UserAgent` bucket with a fixed gray legend dot and slice color.

## 4. Verification

- [x] 4.1 Add or update focused backend tests for unknown user-agent aggregation and filtering.
- [x] 4.2 Add or update focused frontend tests for the `Unknown` user-agent legend rendering.
- [x] 4.3 Run `uv run pytest -q tests/unit/test_reports_repository.py::test_aggregate_by_useragent_groups_null_as_unknown_and_excludes_blank_groups tests/integration/test_reports_api.py::test_reports_api_supports_useragent_group_filter_and_breakdown`.
- [x] 4.4 Run `bun run test src/features/reports/components/useragent-distribution-donut.test.tsx src/features/reports/components/model-distribution-donut.test.tsx`.
- [x] 4.5 Run `uv run openspec validate fix-reports-unknown-useragent-bucket --strict`.
- [x] 4.6 Run `uv run openspec validate --specs`.
