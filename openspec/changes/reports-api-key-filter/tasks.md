## Tasks

- [ ] Backend: Add `api_key_id` query param to `GET /api/reports` in `app/modules/reports/api.py`.
- [ ] Backend: Thread `api_key_ids` through `ReportsService` in `app/modules/reports/service.py`.
- [ ] Backend: Add `RequestLog.api_key_id.in_(...)` condition to `_report_conditions` and bypass rollup when filtered in `app/modules/reports/repository.py`.
- [ ] Frontend: Extend `ReportsParams` in `frontend/src/features/reports/api.ts`.
- [ ] Frontend: Add API key `MultiSelectFilter` in `reports-filters.tsx` and wire options in `reports-page.tsx`.
- [ ] Tests: Add backend route test for `api_key_id` filtering and frontend filter component test.
