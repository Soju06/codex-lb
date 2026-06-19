## 1. OpenSpec

- [x] 1.1 proposal.md
- [x] 1.2 spec deltas
- [x] 1.3 tasks.md

## 2. Read path

- [x] 2.1 Extend `UsagePayload` with `rate_limit_reset_credits`
- [x] 2.2 Alembic migration + `UsageHistory.rate_limit_reset_available_count`
- [x] 2.3 Persist count in `UsageUpdater` primary `add_entry`
- [x] 2.4 Expose on `AccountSummary` and frontend schema

## 3. Write path

- [x] 3.1 `app/core/clients/rate_limit_reset.py`
- [x] 3.2 `AccountsService.apply_usage_reset`
- [x] 3.3 `POST /api/accounts/{id}/usage-reset/apply`

## 4. Dashboard

- [x] 4.1 API hook + mutation
- [x] 4.2 Apply reset button + ConfirmDialog
- [x] 4.3 Availability badge on account list

## 5. Tests and docs

- [x] 5.1 Unit + integration tests
- [x] 5.2 Frontend tests + MSW handlers
- [x] 5.3 usage-refresh-policy context + CHANGELOG