## 1. Pricing rules

- [x] 1.1 Add `gpt-5.4` pricing rules and aliases
- [x] 1.2 Apply non-standard service-tier pricing when `service_tier` indicates priority/fast or flex mode

## 2. Cost accounting

- [x] 2.1 Persist `service_tier` on request logs
- [x] 2.2 Use `service_tier` in API key cost-limit settlement
- [x] 2.3 Use `service_tier` in request-log and usage cost summaries
- [x] 2.4 Expose `service_tier` in request-log API responses and dashboard labels

## 3. Regression coverage

- [x] 3.1 Add unit tests for pricing resolution and tiered cost math
- [x] 3.2 Add unit tests for dashboard and API key cost accounting
- [x] 3.3 Add integration test for request-log fast-mode cost reporting
- [x] 3.4 Add frontend tests for request-log service-tier display

## 4. Spec updates

- [x] 4.1 Add API key cost-accounting requirement for service-tier pricing
- [x] 4.2 Add request-log visibility requirement for `service_tier`
