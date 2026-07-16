## 1. Schema And Persistence

- [x] 1.1 Add an Alembic migration that creates nullable `request_logs.useragent` and `request_logs.useragent_group` columns plus an index on `useragent_group`.
- [x] 1.2 Update `app/db/models.py` so `RequestLog` exposes the new nullable fields.
- [x] 1.3 Update `app/modules/request_logs/repository.py` so `add_log()` accepts and persists `useragent` and `useragent_group`.
- [x] 1.4 Add repository or integration coverage that persists the full header string and derived group, and preserves `null` for missing or blank headers.

## 2. Proxy Capture

- [x] 2.1 Add proxy-side normalization logic that resolves the full `User-Agent` header and derived group from inbound headers.
- [x] 2.2 Thread the normalized values through `_write_request_log()` and `_persist_request_log()` in `app/modules/proxy/service.py`.
- [x] 2.3 Update HTTP request-log call sites to persist the new values.
- [x] 2.4 Update WebSocket request-log call sites to persist the new values.
- [x] 2.5 Add unit coverage for HTTP and WebSocket request-log writes plus missing or blank header behavior.

## 3. Dashboard API And UI

- [x] 3.1 Extend backend request-log schemas and mappers to expose `useragent` and `useragentGroup`.
- [x] 3.2 Extend frontend request-log schemas and test factories with the new nullable fields.
- [x] 3.3 Add the `User Agent` field to the Request Details dialog below the `Transport`, `Time`, and `Error Code` row.
- [x] 3.4 Add frontend tests for the populated and null Request Details states.

## 4. Spec And Verification

- [x] 4.1 Validate the OpenSpec change and fix any formatting or schema issues.
- [x] 4.2 Run the targeted backend and frontend test suites for this change.
- [x] 4.3 Run the repo checks needed for touched files and confirm the worktree only contains intended changes for this feature.
