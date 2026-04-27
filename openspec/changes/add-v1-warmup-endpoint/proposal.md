## Why

Operators need a way to proactively warm account pools before real traffic arrives, so first request latency and upstream cold-start behavior do not impact users. The current proxy has no API-level warmup flow tied to API-key account pools or usage-aware gating.

## What Changes

- Add `POST /v1/warmup` with API-key authentication behavior aligned to existing `/v1/*` proxy routes.
- Add warmup modes:
  - `default`: warm only accounts with a usable primary (5h) usage row and 100% remaining.
  - `all-or-none`: reject unless every target account satisfies the same eligibility.
  - `force-update`: warm all target accounts regardless of usage.
- Add configurable warmup model setting with default `gpt-5.4-mini`; this setting determines the upstream warmup request `model` value (it is not an upstream `warmup_model` field).
- Run warmup fan-out in parallel with a bounded max concurrency of 5 accounts per execution.
- Persist and expose warmup requests in request logs as a distinct request kind.
- Surface warmup rows in dashboard request logs while excluding warmup traffic from aggregate dashboard error/request/cost metrics.
- Exclude warmup traffic from API-key usage accounting and API-key usage trend/summary endpoints.

## Capabilities

### New Capabilities
- `proxy-warmup`: API endpoint, mode semantics, execution flow, and warmup visibility/analytics rules.

### Modified Capabilities
- `api-keys`: API-key usage accounting requirements are updated so warmup requests are explicitly excluded from key usage counters and derived key usage summaries.

## Impact

- Backend proxy API/service flow (`/v1/warmup` route, warmup execution, request logging metadata).
- Settings model/repository/service/API and dashboard settings UI/schema for `warmup_model`.
- Request log model/schema/mappers/frontend rendering to expose warmup rows.
- Dashboard and API-key aggregate query paths to exclude warmup traffic from counts, rates, and totals.
- DB migrations for `dashboard_settings.warmup_model` and `request_logs.request_kind`.
- Unit/integration/frontend tests for endpoint modes, logging visibility, and accounting exclusions.
