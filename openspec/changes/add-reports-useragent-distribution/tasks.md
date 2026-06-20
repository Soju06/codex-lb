## 1. Spec

- [x] 1.1 Add a `frontend-architecture` delta covering the `UserAgent` filter, the `useragent_group` request contract, shared relaxed reports-query option discovery, the `Distribution by UserAgent` section, and the `cost` / `req` toggles.

## 2. Backend Reports Contract

- [x] 2.1 Extend `GET /api/reports` to accept a `useragent_group` filter parameter and apply normalized `request_logs.useragent_group` matching.
- [x] 2.2 Extend reports response schemas so `byModel` includes `requests` and the payload includes `byUseragent`.
- [x] 2.3 Aggregate reports by `request_logs.useragent_group` and apply the optional filter across all report aggregates.

## 3. Frontend Reports Wiring

- [x] 3.1 Extend reports frontend schemas and query wiring so `/reports` sends `useragent_group` on filtered `GET /api/reports` requests and consumes the new response fields.
- [x] 3.2 Add a visible `UserAgent` filter beside `Model` using normalized `request_logs.useragent_group` values, and keep filter-option discovery on the same relaxed `GET /api/reports` query already used for report filter choices.
- [x] 3.3 Render `Distribution by UserAgent` below `Distribution by Model`.
- [x] 3.4 Add independent `cost` / `req` toggles to both distribution cards, defaulting to `cost`.

## 4. Verification

- [x] 4.1 Add or update focused backend tests for user-agent aggregation and filtering.
- [x] 4.2 Add or update focused frontend tests for reports schemas, query params, filters, page wiring, and both distribution cards.
- [x] 4.3 Run `openspec validate add-reports-useragent-distribution --strict`.
- [x] 4.4 Run the focused backend and frontend reports test targets.
- [x] 4.5 Run `bun run typecheck`.
- [x] 4.6 Run `uv run openspec validate --specs`.
