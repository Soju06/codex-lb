## Why

The operator wants remaining plan time directly beneath the status badge in the original Detail selector, and a much shorter List view that excludes request/token/credential metadata.

## What Changes

- Add a compact recorded plan duration such as `18d 08h` below each status badge in the original Detail selector.
- Reduce List rows to identity/workspace, plan/status, remaining plan time and primary/weekly (or monthly-only) quota/reset timing. Move request totals, credentials, credits, warm-up details and long timestamps out of List rows; they remain in Grid and selected details.
- Preserve the original left selector/right chart layout, filters, pagination, privacy, selection and management.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `frontend-architecture`: Define a minimal List surface distinct from the full Grid overview.
- `account-subscription-term`: Define a short recorded-plan duration beneath selector status and in List rows.

## Impact

Frontend presentation, translations, screenshots and regression tests only. No backend, token collection, API contract or deployment change.
