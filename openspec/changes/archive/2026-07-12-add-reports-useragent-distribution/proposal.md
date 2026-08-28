## Why

Operators can currently filter reports by account and model, and can only see one distribution card grouped by model. Request logs already persist normalized `useragent_group` values, but the reports surface cannot filter or summarize usage by client type.

## What Changes

- Add a `UserAgent` filter to `/reports` beside the existing `Model` filter, using normalized `request_logs.useragent_group` values.
- Extend `GET /api/reports` so reports can be filtered by an explicit `useragent_group` request parameter.
- Extend the reports payload with `byUseragent` entries and request counts for `byModel` entries.
- Keep `UserAgent` filter option discovery on the same relaxed `GET /api/reports` query already used to populate report filter choices, rather than adding a separate endpoint.
- Add a `Distribution by UserAgent` card below `Distribution by Model`.
- Add a compact `cost` / `req` toggle to both distribution cards, defaulting to `cost`.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: `/reports` now exposes a user-agent filter, an additional user-agent distribution card, and switchable cost/request distribution views.

## Impact

- Backend: `app/modules/reports/*` request filtering and aggregation.
- Frontend: `frontend/src/features/reports/*` schemas, filters, page wiring, and distribution components.
- Tests: backend reports repository/service/API coverage plus focused frontend reports schema, hook, filter, page, and donut tests.
