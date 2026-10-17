## Why

Users need the ability to filter the Reports dashboard by specific API keys (matching the Request Logs filter) so summary, daily trends, model, and account metrics can be narrowed to specific API key traffic.

## What Changes

- Add optional repeatable `api_key_id` query parameter to `GET /api/reports`.
- Filter all report aggregations (summary, daily rows, model distribution, account distribution) by `RequestLog.api_key_id`.
- Add API key multi-select dropdown to the Reports dashboard filter bar.

## Capabilities

### Modified Capabilities
- `reports`: Add `api_key_id` filtering support across endpoint and UI.
